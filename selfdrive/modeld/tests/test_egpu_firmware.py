"""Persistent AMD firmware for precompiled eGPU runtimes (offline after a reboot)."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpilot.selfdrive.modeld import egpu_firmware as F

NAMES = F.AMD_FIRMWARE['gfx1200']


def stored(root: Path) -> dict[str, bytes]:
  return {p.name: p.read_bytes() for p in (root / F.FIRMWARE_DIR).iterdir() if p.name != F.HASHES}


def fake_runtime(path: Path, served: dict[str, bytes], fail: bool = False) -> Path:
  """A minimal runtime layout: fetch_fw/hashes as the pinned tinygrad provides them."""
  (path / 'tinygrad/runtime/autogen/am').mkdir(parents=True)
  (path / 'tinygrad/runtime/support/am').mkdir(parents=True)
  for package in ('tinygrad/runtime', 'tinygrad/runtime/autogen', 'tinygrad/runtime/support', 'tinygrad/runtime/support/am'):
    (path / package / '__init__.py').write_text('')
  (path / 'tinygrad/__init__.py').write_text('')
  hashes = {name: hashlib.sha256(blob).hexdigest() for name, blob in served.items()}
  (path / 'tinygrad/runtime/autogen/am/__init__.py').write_text('')
  (path / 'tinygrad/runtime/autogen/am/fw.py').write_text(f'hashes = {hashes!r}\n')
  (path / 'tinygrad/helpers.py').write_text(
    'import json, os\n'
    f'SERVED = {json.dumps({k: v.decode() for k, v in served.items()})}\n'
    'def fetch_fw(path, name, sha256):\n'
    f'  if {fail!r}: raise OSError("network unreachable")\n'
    '  log = os.environ.get("FW_LOG")\n'
    '  if log: open(log, "a").write(name + "\\n")\n'
    '  return SERVED[name].encode()\n')
  (path / 'tinygrad/runtime/support/am/amdev.py').write_text('from tinygrad.helpers import fetch_fw\n')
  return path


@pytest.fixture
def blobs():
  return {name: f'firmware {name}'.encode() for name in NAMES}


def test_fixture_runtimes_without_firmware_pins_are_unaffected(tmp_path):
  runtime = tmp_path / 'runtime'
  (runtime / 'tinygrad').mkdir(parents=True)
  assert not F.applies(runtime)
  assert F.ready(tmp_path, runtime, 'gfx1200')
  F.prefetch(tmp_path, runtime, 'gfx1200')  # no-op
  assert not (tmp_path / F.FIRMWARE_DIR).exists()


def test_prefetch_stores_verified_firmware_once(tmp_path, blobs, monkeypatch):
  runtime = fake_runtime(tmp_path / 'runtime', blobs)
  log = tmp_path / 'fetch.log'
  monkeypatch.setenv('FW_LOG', str(log))
  assert not F.ready(tmp_path, runtime, 'gfx1200')
  F.prefetch(tmp_path, runtime, 'gfx1200')
  assert F.ready(tmp_path, runtime, 'gfx1200')
  assert stored(tmp_path) == blobs
  assert sorted(log.read_text().split()) == sorted(NAMES)
  F.prefetch(tmp_path, runtime, 'gfx1200')  # verified copies are reused
  assert len(log.read_text().split()) == len(NAMES)


def test_offline_prefetch_is_a_retryable_network_error(tmp_path, blobs):
  runtime = fake_runtime(tmp_path / 'runtime', blobs, fail=True)
  with pytest.raises(ConnectionError, match='GPU firmware unavailable'):
    F.prefetch(tmp_path, runtime, 'gfx1200')
  assert not F.ready(tmp_path, runtime, 'gfx1200')


def test_unknown_architecture_is_never_ready(tmp_path, blobs):
  runtime = fake_runtime(tmp_path / 'runtime', blobs)
  assert not F.ready(tmp_path, runtime, 'gfx9999')
  with pytest.raises(ValueError):
    F.prefetch(tmp_path, runtime, 'gfx9999')


def test_worker_hook_serves_stored_firmware_without_network(tmp_path, blobs):
  runtime = fake_runtime(tmp_path / 'runtime', blobs)
  F.prefetch(tmp_path, runtime, 'gfx1200')
  (tmp_path / F.FIRMWARE_DIR / NAMES[0]).write_bytes(b'corrupt')  # a bad copy falls back to the runtime
  code = f'''
import sys; sys.path.insert(0, {str(runtime)!r})
from tinygrad import helpers
calls = []
def original(path, name, sha256):
  calls.append(name)
  return helpers.SERVED[name].encode()
helpers.fetch_fw = original
from openpilot.selfdrive.modeld.egpu_firmware import install_fetch_hook, AMD_FIRMWARE
from pathlib import Path
install_fetch_hook(Path({str(tmp_path)!r}))
from tinygrad.runtime.support.am import amdev
from tinygrad.runtime.autogen.am import fw
for name in AMD_FIRMWARE["gfx1200"]:
  assert amdev.fetch_fw("amdgpu", name, fw.hashes[name]) == helpers.SERVED[name].encode()
assert calls == [AMD_FIRMWARE["gfx1200"][0]], calls
print("ok")
'''
  root = Path(__file__).resolve().parents[3]
  result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                          env=dict(os.environ, PYTHONPATH=str(root)))
  assert result.stdout.strip() == 'ok', result.stderr


def test_installed_requires_firmware_and_install_prefetches_it(tmp_path, blobs, monkeypatch):
  from openpilot.selfdrive.modeld import precompiled_model as P
  root = tmp_path / 'precompiled' / ('a' * 64)
  runtime_sha = 'b' * 64
  runtime = fake_runtime(root / ('runtime-' + runtime_sha[:16]), blobs)
  (runtime / 'examples/openpilot').mkdir(parents=True)
  (runtime / 'examples/openpilot/compile_warp.py').write_text('')
  (root / 'model.pkl').write_bytes(b'x' * 10)
  value = {'runtime_directory': runtime.name, 'catalog_url': 'https://example.com/precompiled.json',
           'format': 'comma-generic-onnx', 'gpu_arch': 'gfx1200', 'pickle': {'size': 10}, 'runtime': {'sha256': runtime_sha}}
  (root / 'installed.json').write_text(json.dumps(value))
  monkeypatch.setattr(P, 'validate_catalog', lambda value, sha, url: value)
  model = SimpleNamespace(sha256='a' * 64)
  assert P.installed(model, tmp_path) is None
  F.prefetch(root, runtime, 'gfx1200')
  assert P.installed(model, tmp_path) == root / 'model.pkl'
  source = Path(P.__file__).read_text()
  assert source.index("egpu_firmware.prefetch(root, runtime, value['gpu_arch'])") < source.index("os.replace(marker, root / 'installed.json')")


def sizeless_zst(blob: bytes) -> bytes:
  zstandard = pytest.importorskip('zstandard')
  data = zstandard.ZstdCompressor(write_content_size=False).compress(blob)
  with pytest.raises(zstandard.ZstdError):
    zstandard.decompress(data)  # what the pinned runtime's fetch_fw would call
  return data


def test_agnos_18_4_sizeless_os_firmware_is_used_and_stale_blobs_are_downloaded(tmp_path, blobs, monkeypatch):
  runtime = fake_runtime(tmp_path / 'runtime', blobs)
  os_root = tmp_path / 'os-firmware'
  (os_root / 'amdgpu').mkdir(parents=True)
  stale = set(NAMES[:4])  # AGNOS 18.4 ships older me/mec/pfp/smu than the runtimes pin
  for name in NAMES:
    (os_root / 'amdgpu' / f'{name}.zst').write_bytes(sizeless_zst(b'old ' + blobs[name] if name in stale else blobs[name]))
  log = tmp_path / 'fetch.log'
  monkeypatch.setenv('FW_LOG', str(log))
  monkeypatch.setenv(F.OS_FIRMWARE_ROOT_ENV, str(os_root))
  F.prefetch(tmp_path, runtime, 'gfx1200')
  assert stored(tmp_path) == blobs
  assert set(log.read_text().split()) == stale


def test_worker_hook_reads_sizeless_os_firmware_without_a_stored_copy(tmp_path, blobs):
  runtime = fake_runtime(tmp_path / 'runtime', blobs)
  os_root = tmp_path / 'os-firmware'
  (os_root / 'amdgpu').mkdir(parents=True)
  for name in NAMES:
    (os_root / 'amdgpu' / f'{name}.zst').write_bytes(sizeless_zst(blobs[name]))
  code = f'''
import sys; sys.path.insert(0, {str(runtime)!r})
from tinygrad import helpers
def original(path, name, sha256): raise RuntimeError("download attempted")
helpers.fetch_fw = original
from openpilot.selfdrive.modeld.egpu_firmware import install_fetch_hook, AMD_FIRMWARE
from pathlib import Path
install_fetch_hook(Path({str(tmp_path / 'no-store')!r}))
from tinygrad.runtime.support.am import amdev
from tinygrad.runtime.autogen.am import fw
for name in AMD_FIRMWARE["gfx1200"]:
  assert amdev.fetch_fw("amdgpu", name, fw.hashes[name]) == helpers.SERVED[name].encode()
print("ok")
'''
  root = Path(__file__).resolve().parents[3]
  result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                          env=dict(os.environ, PYTHONPATH=str(root), **{F.OS_FIRMWARE_ROOT_ENV: str(os_root)}))
  assert result.stdout.strip() == 'ok', result.stderr


def test_corrupt_stored_firmware_is_not_ready_and_is_repaired(tmp_path, blobs):
  runtime = fake_runtime(tmp_path / 'runtime', blobs)
  F.prefetch(tmp_path, runtime, 'gfx1200')
  (tmp_path / F.FIRMWARE_DIR / 'smu_14_0_2.bin').write_bytes(b'damaged')
  assert not F.ready(tmp_path, runtime, 'gfx1200')
  F.prefetch(tmp_path, runtime, 'gfx1200')
  assert F.ready(tmp_path, runtime, 'gfx1200') and stored(tmp_path) == blobs
  (tmp_path / F.FIRMWARE_DIR / F.HASHES).unlink()
  assert not F.ready(tmp_path, runtime, 'gfx1200')


def hook_result(tmp_path, runtime, os_root, code_tail):
  code = f'''
import sys; sys.path.insert(0, {str(runtime)!r})
from tinygrad import helpers
def original(path, name, sha256): raise RuntimeError("runtime fetch_fw must not read amdgpu blobs")
helpers.fetch_fw = original
from openpilot.selfdrive.modeld.egpu_firmware import install_fetch_hook, AMD_FIRMWARE
from pathlib import Path
install_fetch_hook(Path({str(tmp_path)!r}), Path({str(runtime)!r}))
from tinygrad.runtime.support.am import amdev
from tinygrad.runtime.autogen.am import fw
{code_tail}
'''
  root = Path(__file__).resolve().parents[3]
  return subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                        env=dict(os.environ, PYTHONPATH=str(root), **{F.OS_FIRMWARE_ROOT_ENV: str(os_root)}))


def test_worker_repairs_damaged_store_instead_of_decoding_bad_os_firmware(tmp_path, blobs):
  runtime = fake_runtime(tmp_path / 'runtime', blobs)
  F.prefetch(tmp_path, runtime, 'gfx1200')
  (tmp_path / F.FIRMWARE_DIR / 'smu_14_0_2.bin').write_bytes(b'damaged')
  os_root = tmp_path / 'os-firmware'
  (os_root / 'amdgpu').mkdir(parents=True)
  for name in NAMES:  # AGNOS 18.4: sizeless frames, and a stale smu blob
    (os_root / 'amdgpu' / f'{name}.zst').write_bytes(sizeless_zst(b'old ' + blobs[name] if name == 'smu_14_0_2.bin' else blobs[name]))
  result = hook_result(tmp_path, runtime, os_root, '''
for name in AMD_FIRMWARE["gfx1200"]:
  assert amdev.fetch_fw("amdgpu", name, fw.hashes[name]) == helpers.SERVED[name].encode(), name
print("ok")
''')
  assert result.stdout.strip() == 'ok', result.stderr
  assert F.ready(tmp_path, runtime, 'gfx1200') and stored(tmp_path) == blobs


def test_worker_reports_unavailable_firmware_without_network(tmp_path, blobs):
  runtime = fake_runtime(tmp_path / 'runtime', blobs, fail=True)
  result = hook_result(tmp_path, runtime, tmp_path / 'no-os-firmware', '''
try:
  amdev.fetch_fw("amdgpu", "smu_14_0_2.bin", fw.hashes["smu_14_0_2.bin"])
except RuntimeError as exc:
  print(exc)
''')
  assert result.stdout.strip() == 'GPU firmware unavailable: smu_14_0_2.bin', result.stderr
  assert F.failure('RuntimeError: ' + result.stdout.strip())


@pytest.mark.parametrize('error', ['zstd.ZstdError: could not determine content size in frame header',
                                   'Traceback (most recent call last):\n  File "x"\nzstandard.backend_c.ZstdError: could not determine content size in frame header',
                                   'RuntimeError: GPU firmware unavailable: smu_14_0_2.bin'])
def test_firmware_failures_never_block_the_artifact_and_old_blocks_retry(tmp_path, monkeypatch, error):
  from openpilot.selfdrive.modeld import egpu_worker_progress, precompiled_model as pm
  value = {'pickle': {'sha256': 'a' * 64}, 'runtime': {'sha256': 'b' * 64}}
  (tmp_path / 'installed.json').write_text(json.dumps(value))
  monkeypatch.setattr(egpu_worker_progress, 'boot_identity', lambda: 'first')
  assert not pm.record_failure(tmp_path / 'model.pkl', error, 'load')
  assert not (tmp_path / 'rejected').exists()
  # A block written by the earlier port (cb78f7b0) is retried once on the next boot.
  failure = json.loads((tmp_path / 'last_failure.json').read_text()) | {'rejected': True, 'classification': 'artifact_blocked'}
  (tmp_path / 'last_failure.json').write_text(json.dumps(failure))
  assert not pm.retry_usb_rejection(tmp_path, 'a' * 64)
  monkeypatch.setattr(egpu_worker_progress, 'boot_identity', lambda: 'second')
  assert pm.retry_usb_rejection(tmp_path, 'a' * 64)


@pytest.mark.parametrize('error', ['ValueError: precompiled PKL checksum mismatch', 'ZstdError: other message',
                                   'ValueError: quoted GPU firmware unavailable'])
def test_unrelated_errors_are_not_firmware_failures(error):
  assert not F.failure(error)


def test_complete_install_without_firmware_record_is_restored_offline(tmp_path, blobs, monkeypatch):
  from openpilot.selfdrive.modeld import precompiled_model as P
  model_sha, runtime_sha = 'a' * 64, None
  root = tmp_path / 'precompiled' / model_sha
  root.mkdir(parents=True)
  (root / 'model.pkl').write_bytes(b'x' * 10)
  (root / 'runtime.tar.gz').write_bytes(b'runtime archive')
  runtime_sha = hashlib.sha256(b'runtime archive').hexdigest()
  runtime = fake_runtime(root / ('runtime-' + runtime_sha[:16]), blobs, fail=True)  # any download would fail
  (runtime / 'examples/openpilot').mkdir(parents=True)
  (runtime / 'examples/openpilot/compile_warp.py').write_text('')
  value = {'runtime_directory': runtime.name, 'catalog_url': 'https://example.com/precompiled.json', 'format': 'comma-generic-onnx',
           'gpu_arch': 'gfx1200', 'pickle': {'size': 10, 'sha256': hashlib.sha256(b'x' * 10).hexdigest()},
           'runtime': {'sha256': runtime_sha}}
  (root / 'installed.json').write_text(json.dumps(value))
  (root / F.FIRMWARE_DIR).mkdir()
  for name, blob in blobs.items():  # a56cdeac-style store: verified blobs, no hashes.json
    (root / F.FIRMWARE_DIR / name).write_bytes(blob)
  monkeypatch.setattr(P, 'validate_catalog', lambda value, sha, url: value)
  monkeypatch.setattr(P, 'urlopen', lambda *a, **k: pytest.fail('network used'))
  model = SimpleNamespace(sha256=model_sha, url='https://example.com/model.pkl')
  assert P.installed(model, tmp_path) is None
  assert P.ensure_precompiled(model, tmp_path) == root / 'model.pkl'
  assert (root / F.FIRMWARE_DIR / F.HASHES).is_file()
  # A missing blob still needs the network: offline restore declines instead of downloading.
  (root / F.FIRMWARE_DIR / F.HASHES).unlink()
  (root / F.FIRMWARE_DIR / NAMES[0]).unlink()
  assert P.restore_firmware_record(root, model, tmp_path) is None
