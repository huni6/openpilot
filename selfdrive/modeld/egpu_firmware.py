"""Keep the AMD GPU firmware that a pinned eGPU runtime expects on persistent storage.

tinygrad's userspace AMD driver (runtime/support/am/amdev.py) loads these blobs
through tinygrad.helpers.fetch_fw: first /lib/firmware/amdgpu/<name>.zst when its
hash matches the runtime's pin, otherwise a download from linux-firmware into
~/.cache. That fails on this branch's AGNOS 18.4 in two ways: its .zst frames omit
their content size, which zstandard.decompress() rejects with an error that is not
caught, and several blobs predate the runtimes' pins. ~/.cache is also an overlay
on tmpfs, so a download would be needed again on every boot. Instead, the verified
blobs are stored next to the installed artifact while delivery has network access,
and the worker reads them from there.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys

FIRMWARE_DIR = 'amdgpu-firmware'
# Files amdev.py loads, named by the GPU's IP versions (RX 9060 XT / Navi 44).
AMD_FIRMWARE = {
  'gfx1200': ('psp_14_0_2_sos.bin', 'smu_14_0_2.bin', 'sdma_7_0_0.bin', 'gc_12_0_0_pfp.bin', 'gc_12_0_0_me.bin',
              'gc_12_0_0_mec.bin', 'gc_12_0_0_imu.bin', 'gc_12_0_0_rlc.bin'),
}
RUNTIME_FW_PINS = Path('tinygrad/runtime/autogen/am/fw.py')
# Overridable only so tests can stand in for the read-only OS firmware directory.
OS_FIRMWARE_ROOT_ENV = 'CARROT_OS_FIRMWARE_ROOT'

_PREFETCH = r'''
import hashlib, os, pathlib, sys, tempfile
runtime, out, names = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3:]
sys.path.insert(0, runtime)
from tinygrad import helpers
from tinygrad.runtime.autogen.am import fw

def os_blob(name):
  # AGNOS 18.4 frames omit their content size; zstandard.decompress() (what the
  # runtime uses) rejects them, so read the OS copy as a stream instead.
  path = pathlib.Path(os.environ.get('CARROT_OS_FIRMWARE_ROOT', '/lib/firmware')) / 'amdgpu' / (name + '.zst')
  if not path.is_file():
    return None
  try:
    import zstandard
    return zstandard.ZstdDecompressor().stream_reader(path.read_bytes()).read()
  except Exception:
    return None

real_is_file = pathlib.Path.is_file
def skip_os_firmware(self):
  # Make the runtime's fetch_fw go straight to its pinned linux-firmware download.
  return False if str(self).startswith('/lib/firmware/') else real_is_file(self)

out.mkdir(parents=True, exist_ok=True)
for name in names:
  want = fw.hashes[name]
  target = out / name
  if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == want:
    continue
  blob = os_blob(name)
  if blob is None or hashlib.sha256(blob).hexdigest() != want:
    pathlib.Path.is_file = skip_os_firmware
    try:
      blob = helpers.fetch_fw('amdgpu', name, want)
    finally:
      pathlib.Path.is_file = real_is_file
  if hashlib.sha256(blob).hexdigest() != want:
    raise ValueError(f'firmware hash mismatch: {name}')
  fd, tmp = tempfile.mkstemp(dir=out, prefix='.fw-')
  with os.fdopen(fd, 'wb') as f:
    f.write(blob)
    f.flush()
    os.fsync(f.fileno())
  os.replace(tmp, target)
'''


def applies(runtime: Path) -> bool:
  """Only real AMD runtimes pin firmware; test fixtures and other formats do not."""
  return (runtime / RUNTIME_FW_PINS).is_file()


def firmware_names(gpu_arch: str) -> tuple[str, ...]:
  if gpu_arch not in AMD_FIRMWARE:
    raise ValueError(f'no firmware list for GPU architecture {gpu_arch!r}')
  return AMD_FIRMWARE[gpu_arch]


def ready(root: Path, runtime: Path, gpu_arch: str) -> bool:
  if not applies(runtime):
    return True
  try:
    names = firmware_names(gpu_arch)
  except ValueError:
    return False
  return all((root / FIRMWARE_DIR / name).is_file() for name in names)


def prefetch(root: Path, runtime: Path, gpu_arch: str, timeout: float = 300) -> None:
  """Store the runtime's pinned firmware under root; raises ConnectionError when it cannot."""
  if not applies(runtime):
    return
  names = firmware_names(gpu_arch)
  env = dict(os.environ)
  env.pop('XDG_CACHE_HOME', None)
  try:
    subprocess.run([sys.executable, '-I', '-c', _PREFETCH, str(runtime), str(root / FIRMWARE_DIR), *names],
                   env=env, check=True, timeout=timeout, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
  except subprocess.CalledProcessError as exc:
    detail = (exc.stdout or b'').decode('utf-8', 'replace').strip().splitlines()
    raise ConnectionError(f'GPU firmware unavailable: {detail[-1] if detail else exc}') from exc
  except subprocess.TimeoutExpired as exc:
    raise ConnectionError('GPU firmware download timed out') from exc


def install_fetch_hook(root: Path) -> None:
  """Inside the worker, before the AMD driver loads: prefer the verified stored copies."""
  from tinygrad import helpers
  original = helpers.fetch_fw
  stored = root / FIRMWARE_DIR

  def fetch_fw(path: str, name: str, sha256: str) -> bytes:
    candidate = stored / name
    if path == 'amdgpu' and candidate.is_file():
      blob = candidate.read_bytes()
      if hashlib.sha256(blob).hexdigest() == sha256:
        return blob
    # Without a stored copy, still accept an OS blob whose frame omits its size.
    os_copy = Path(os.environ.get(OS_FIRMWARE_ROOT_ENV, '/lib/firmware')) / path / (name + '.zst')
    if os_copy.is_file():
      try:
        import zstandard
        blob = zstandard.ZstdDecompressor().stream_reader(os_copy.read_bytes()).read()
        if hashlib.sha256(blob).hexdigest() == sha256:
          return blob
      except Exception:
        pass
    return original(path, name, sha256)

  helpers.fetch_fw = fetch_fw
  amdev = sys.modules.get('tinygrad.runtime.support.am.amdev')
  if amdev is not None:
    amdev.fetch_fw = fetch_fw
