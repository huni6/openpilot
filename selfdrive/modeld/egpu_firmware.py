"""Keep the AMD GPU firmware that a pinned eGPU runtime expects on persistent storage.

tinygrad's userspace AMD driver (runtime/support/am/amdev.py) loads these blobs
through tinygrad.helpers.fetch_fw: first /lib/firmware/amdgpu/<name>.zst when its
hash matches the runtime's pin, otherwise a download from linux-firmware into
~/.cache. That fails on this branch's AGNOS 18.4 in two ways: its .zst frames omit
their content size, which zstandard.decompress() rejects with an error that is not
caught, and several blobs predate the runtimes' pins. ~/.cache is also an overlay
on tmpfs, so a download would be needed again on every boot. Instead, the verified
blobs are stored next to the installed artifact while delivery has network access,
and the worker reads them from there. The runtime's own fetch_fw never reads the
OS copy directly.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

FIRMWARE_DIR = 'amdgpu-firmware'
HASHES = 'hashes.json'
UNAVAILABLE = 'GPU firmware unavailable'
# Files amdev.py loads, named by the GPU's IP versions (RX 9060 XT / Navi 44).
AMD_FIRMWARE = {
  'gfx1200': ('psp_14_0_2_sos.bin', 'smu_14_0_2.bin', 'sdma_7_0_0.bin', 'gc_12_0_0_pfp.bin', 'gc_12_0_0_me.bin',
              'gc_12_0_0_mec.bin', 'gc_12_0_0_imu.bin', 'gc_12_0_0_rlc.bin'),
}
RUNTIME_FW_PINS = Path('tinygrad/runtime/autogen/am/fw.py')
# Overridable only so tests can stand in for the read-only OS firmware directory.
OS_FIRMWARE_ROOT_ENV = 'CARROT_OS_FIRMWARE_ROOT'

_PREFETCH = r'''
import hashlib, json, os, pathlib, sys, tempfile
runtime, out, names = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3:]
sys.path.insert(0, runtime)
from tinygrad import helpers
from tinygrad.runtime.autogen.am import fw

def sha(data):
  return hashlib.sha256(data).hexdigest()

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

def atomic(target, data):
  fd, tmp = tempfile.mkstemp(dir=target.parent, prefix='.fw-')
  with os.fdopen(fd, 'wb') as f:
    f.write(data)
    f.flush()
    os.fsync(f.fileno())
  os.replace(tmp, target)

out.mkdir(parents=True, exist_ok=True)
try:
  recorded = json.loads((out / 'hashes.json').read_text())
  recorded = recorded if isinstance(recorded, dict) else {}
except (OSError, ValueError):
  recorded = {}
for name in names:
  want = fw.hashes[name]
  target = out / name
  if not (target.is_file() and sha(target.read_bytes()) == want):
    blob = os_blob(name)
    if blob is None or sha(blob) != want:
      pathlib.Path.is_file = skip_os_firmware
      try:
        blob = helpers.fetch_fw('amdgpu', name, want)
      finally:
        pathlib.Path.is_file = real_is_file
    if sha(blob) != want:
      raise ValueError(f'firmware hash mismatch: {name}')
    atomic(target, blob)
  recorded[name] = want
atomic(out / 'hashes.json', json.dumps(recorded, indent=1, sort_keys=True).encode())
'''


def _sha(data: bytes) -> str:
  return hashlib.sha256(data).hexdigest()


def applies(runtime: Path) -> bool:
  """Only real AMD runtimes pin firmware; test fixtures and other formats do not."""
  return (runtime / RUNTIME_FW_PINS).is_file()


def firmware_names(gpu_arch: str) -> tuple[str, ...]:
  if gpu_arch not in AMD_FIRMWARE:
    raise ValueError(f'no firmware list for GPU architecture {gpu_arch!r}')
  return AMD_FIRMWARE[gpu_arch]


def ready(root: Path, runtime: Path, gpu_arch: str) -> bool:
  """All blobs are stored and still match the runtime pins recorded when they were stored."""
  if not applies(runtime):
    return True
  try:
    names = firmware_names(gpu_arch)
    recorded = json.loads((root / FIRMWARE_DIR / HASHES).read_text())
    return isinstance(recorded, dict) and all(
      isinstance(recorded.get(name), str) and _sha((root / FIRMWARE_DIR / name).read_bytes()) == recorded[name] for name in names)
  except (OSError, ValueError):
    return False


def prefetch(root: Path, runtime: Path, gpu_arch: str, timeout: float = 300, names: tuple[str, ...] | None = None) -> None:
  """Store the runtime's pinned firmware under root; raises ConnectionError when it cannot."""
  if not applies(runtime):
    return
  names = names or firmware_names(gpu_arch)
  env = dict(os.environ)
  env.pop('XDG_CACHE_HOME', None)
  try:
    subprocess.run([sys.executable, '-I', '-c', _PREFETCH, str(runtime), str(root / FIRMWARE_DIR), *names],
                   env=env, check=True, timeout=timeout, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
  except subprocess.CalledProcessError as exc:
    detail = (exc.stdout or b'').decode('utf-8', 'replace').strip().splitlines()
    raise ConnectionError(f'{UNAVAILABLE}: {detail[-1] if detail else exc}') from exc
  except subprocess.TimeoutExpired as exc:
    raise ConnectionError(f'{UNAVAILABLE}: download timed out') from exc


def install_fetch_hook(root: Path, runtime: Path | None = None) -> None:
  """Inside the worker, before the AMD driver loads: serve verified copies, never the raw OS .zst.

  Order: stored copy, OS copy read as a stream, then repair of the stored copy from
  the runtime's pinned source. The runtime's own fetch_fw is not used for amdgpu
  blobs because it would decompress the OS copy with an uncaught error.
  """
  from tinygrad import helpers
  original = helpers.fetch_fw
  stored = root / FIRMWARE_DIR

  def stored_blob(name: str, sha256: str) -> bytes | None:
    try:
      blob = (stored / name).read_bytes()
    except OSError:
      return None
    return blob if _sha(blob) == sha256 else None

  def os_blob(path: str, name: str, sha256: str) -> bytes | None:
    os_copy = Path(os.environ.get(OS_FIRMWARE_ROOT_ENV, '/lib/firmware')) / path / (name + '.zst')
    try:
      import zstandard
      blob = zstandard.ZstdDecompressor().stream_reader(os_copy.read_bytes()).read()
    except Exception:
      return None
    return blob if _sha(blob) == sha256 else None

  def fetch_fw(path: str, name: str, sha256: str) -> bytes:
    if path != 'amdgpu' or runtime is None:
      return stored_blob(name, sha256) or os_blob(path, name, sha256) or original(path, name, sha256)
    blob = stored_blob(name, sha256) or os_blob(path, name, sha256)
    if blob is None:
      try:
        prefetch(root, runtime, '', names=(name,))
      except ConnectionError as exc:
        raise RuntimeError(f'{UNAVAILABLE}: {name}') from exc
      blob = stored_blob(name, sha256)
    if blob is None:
      raise RuntimeError(f'{UNAVAILABLE}: {name}')
    return blob

  helpers.fetch_fw = fetch_fw
  amdev = sys.modules.get('tinygrad.runtime.support.am.amdev')
  if amdev is not None:
    amdev.fetch_fw = fetch_fw


def failure(detail: str) -> bool:
  """Firmware decode/availability errors are device-setup problems, not a bad artifact."""
  import re
  return re.search(r'^(?:[\w.]+\.)?ZstdError: could not determine content size in frame header\Z|'
                   rf'^(?:RuntimeError: |ConnectionError: )?{UNAVAILABLE}(?:: .*)?\Z',
                   detail.rstrip(), re.MULTILINE) is not None
