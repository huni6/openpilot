"""Overlay an installed carrot-jetson runtime without changing its OS or engine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

RUNTIME = Path('/opt/carrot-jetlink')
SERVICES = ('carrot-jetlink-hud.service', 'carrot-jetlink.service')


def atomic_bytes(path, data):
  temporary = path.with_name(path.name + '.hl-new')
  with temporary.open('wb') as f:
    f.write(data)
    f.flush()
    os.fsync(f.fileno())
  temporary.chmod(0o644)
  temporary.replace(path)


def release_target(runtime, name):
  path = runtime / name
  target = path.resolve(strict=True)
  if target.parent != (runtime / 'releases').resolve() or not target.is_dir():
    raise ValueError('Runtime target must be a release directory')
  return target


def extract_verified(bundle, destination, expected_sha256):
  if hashlib.sha256(bundle.read_bytes()).hexdigest() != expected_sha256:
    raise ValueError('Bundle SHA256 mismatch')
  with tarfile.open(bundle, 'r:gz') as archive:
    members = archive.getmembers()
    names = [m.name for m in members]
    if len(set(names)) != len(names) or len(names) > 3000 or sum(m.size for m in members) > 256 << 20:
      raise ValueError('Invalid bundle size/duplicate paths')
    for member in members:
      path = destination / member.name
      if (not member.isfile() or Path(member.name).is_absolute() or '..' in Path(member.name).parts
          or not path.resolve().is_relative_to(destination.resolve())):
        raise ValueError('Unsafe bundle path')
    manifest_file = archive.extractfile('HL_CLUSTER_MANIFEST.json')
    if manifest_file is None:
      raise ValueError('Missing cluster manifest')
    manifest = json.load(manifest_file)
    if manifest.get('format') != 1 or manifest.get('branch') != 'cluster_jetson':
      raise ValueError('Wrong runtime format')
    expected = manifest['files']
    if set(names) != set(expected) | {'HL_CLUSTER_MANIFEST.json'}:
      raise ValueError('Bundle file manifest mismatch')
    identity = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    if identity != manifest.get('content_sha256'):
      raise ValueError('Bundle content identity mismatch')
    for member in members:
      data = archive.extractfile(member).read()
      if member.name in expected and hashlib.sha256(data).hexdigest() != expected[member.name]:
        raise ValueError('Bundle member checksum mismatch')
      target = destination / member.name
      target.parent.mkdir(parents=True, exist_ok=True)
      target.write_bytes(data)
      target.chmod(0o644)
  return manifest


def check(runtime):
  current = release_target(runtime, 'current')
  for path in ('venv/bin/python', 'cache/last-loaded.json', 'updater/update_host.py'):
    if not (runtime / path).is_file():
      raise ValueError('Not a prepared carrot-jetson runtime: ' + path)
  for path in ('SOURCE_COMMIT', 'tools/jetlink/server.py', 'tools/jetlink/hud.py', 'tools/jetlink/probe_release.py'):
    if not (current / path).is_file():
      raise ValueError('Incomplete upstream runtime: ' + path)
  if (runtime / 'boot-update-required').exists():
    raise ValueError('Boot-gated image requires a separately validated installer; this overlay targets carrot-jetson R2 runtime')
  if (runtime / 'updates/transaction.json').exists():
    raise ValueError('Finish or recover the pending upstream update transaction first')
  return current


def switch(runtime, release):
  target = runtime / 'current.hl-next'
  target.unlink(missing_ok=True)
  target.symlink_to(release.relative_to(runtime))
  os.replace(target, runtime / 'current')


def install(runtime, bundle, expected_sha256, probe):
  previous = check(runtime)
  pin = runtime / 'HL_CLUSTER_PIN.json'
  if pin.exists():
    raise ValueError('A custom runtime is already installed; rollback before replacing it')
  updater = runtime / 'updater/update_host.py'
  backup = updater.with_name('update_host.hl-original.py')
  if backup.exists():
    raise ValueError('Previous updater backup exists; resolve it before installing')
  with tempfile.TemporaryDirectory(prefix='hl-stage-', dir=runtime / 'releases') as temporary:
    staging = Path(temporary)
    overlay = staging / 'overlay'
    overlay.mkdir()
    manifest = extract_verified(bundle, overlay, expected_sha256)
    candidate = staging / 'runtime'
    shutil.copytree(previous, candidate, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for source in overlay.rglob('*'):
      if source.is_file():
        dest = candidate / source.relative_to(overlay)
        if not dest.resolve().is_relative_to(candidate.resolve()):
          raise ValueError('Upstream path redirects outside release')
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        dest.chmod(0o644)
    candidate.chmod(0o755)
    # runuser needs traversal through the TemporaryDirectory as well.
    staging.chmod(0o755)
    remembered = runtime / 'cache/last-loaded.json'
    original_model = remembered.read_bytes()
    try:
      probe(candidate)
    finally:
      if remembered.read_bytes() != original_model:
        atomic_bytes(remembered, original_model)
    target = runtime / 'releases' / ('hl-' + manifest['content_sha256'][:20])
    if target.exists():
      raise ValueError('Candidate release already exists; it has not been overwritten')
    candidate.rename(target)
  atomic_bytes(backup, updater.read_bytes())
  atomic_bytes(updater, (target / 'tools/jetlink/update_guard.py').read_bytes())
  atomic_bytes(pin, json.dumps({'previous': str(previous.relative_to(runtime)),
                               'installed': str(target.relative_to(runtime)), 'manifest': manifest}).encode())
  if hasattr(os, 'sync'):
    os.sync()
  switch(runtime, target)
  if hasattr(os, 'sync'):
    os.sync()
  return target


def rollback(runtime):
  pin = runtime / 'HL_CLUSTER_PIN.json'
  record = json.loads(pin.read_text())
  previous = release_target(runtime, record['previous'])
  installed = release_target(runtime, record['installed'])
  if (runtime / 'current').resolve() not in (previous, installed):
    raise ValueError('Current release changed outside this installer; refusing rollback')
  backup = runtime / 'updater/update_host.hl-original.py'
  if not backup.is_file():
    raise ValueError('Original updater backup is missing')
  switch(runtime, previous)
  atomic_bytes(runtime / 'updater/update_host.py', backup.read_bytes())
  pin.unlink()
  backup.unlink()
  if hasattr(os, 'sync'):
    os.sync()
  return previous


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('action', choices=('check', 'install', 'rollback'))
  parser.add_argument('--bundle', type=Path)
  parser.add_argument('--sha256')
  parser.add_argument('--confirm-parked', action='store_true')
  args = parser.parse_args()
  if not Path('/etc/carrot-jetlink-image.json').is_file():
    raise RuntimeError('This installer is for the prepared ajouatom/carrot-jetson image, not a generic JetPack host')
  current = check(RUNTIME)
  print('Current carrot-jetson runtime:', current)
  if args.action == 'check':
    return
  if os.geteuid() != 0 or not args.confirm_parked:
    parser.error('Use sudo and --confirm-parked with the vehicle parked and comma disconnected')
  if args.action == 'install' and (args.bundle is None or args.sha256 is None):
    parser.error('--bundle and --sha256 are required')
  marker = Path('/etc/carrot-jetlink-protected.json')
  if marker.exists() and json.loads(Path('/run/carrot-storage.json').read_text()).get('state') != 'protected':
    raise RuntimeError('DATA unavailable: never modify the immutable factory recovery image')
  # Share the original updater lock, including across the atomic current switch.
  import fcntl
  with Path('/run/carrot-jetlink-update.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    subprocess.run(['systemctl', 'stop', *SERVICES], check=True)
    try:
      def probe(candidate):
        python = str(RUNTIME / 'venv/bin/python')
        command = ['runuser', '-u', 'jetlink', '--', python]
        subprocess.run([*command, str(candidate / 'tools/jetlink/hud.py'), '--help'], check=True, timeout=60, cwd='/tmp')
        # The image's native probe loads its existing engine and executes test tensors.
        subprocess.run([*command, str(candidate / 'tools/jetlink/probe_release.py'), str(RUNTIME / 'cache')],
                       check=True, timeout=960, cwd='/tmp')
      result = (install(RUNTIME, args.bundle.resolve(), args.sha256, probe) if args.action == 'install' else rollback(RUNTIME))
      print('Selected:', result)
    finally:
      subprocess.run(['systemctl', 'start', *reversed(SERVICES)], check=True)


if __name__ == '__main__':
  main()
