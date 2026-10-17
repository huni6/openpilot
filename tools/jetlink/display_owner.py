"""One USB panel owner; the regular HUD takes priority over boot diagnostics."""
from contextlib import contextmanager, ExitStack
import os
import json
from pathlib import Path
import time

# Runtime updates also work on legacy images without a new systemd drop-in.
DIRECTORY = Path('/dev/shm') / ('carrot-jetlink-display-' + str(getattr(os, 'getuid', lambda: 0)()))
BOOT_USB_LOCK = Path('/run/carrot-jetlink-display/usb.lock')
BOOT_SCRIPT = b'/usr/local/lib/carrot-jetlink/boot-display/tools/jetlink/boot_display.py'


def process_identity(pid):
  # A reused PID from a dead HUD must not suppress diagnostics indefinitely.
  value = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
  return [Path('/proc/sys/kernel/random/boot_id').read_text().strip(), value[19]]


def hud_requested(directory=DIRECTORY):
  try:
    request = json.loads((directory / 'hud-request').read_text())
    pid = int(request['pid'])
    if pid <= 1:
      return False
    return request['identity'] == process_identity(pid)
  except (OSError, ValueError, KeyError, TypeError, IndexError):
    return False


def legacy_boot_owner_paths(directory, proc_root=Path('/proc')):
  """Find the boot renderer's original inode, even after logind unlinks it."""
  if not proc_root.is_dir():
    return
  names = {str(directory / 'owner.lock'), str(directory / 'owner.lock') + ' (deleted)'}
  for process in proc_root.iterdir():
    if not process.name.isdigit():
      continue
    try:
      if process.stat().st_uid != os.getuid() or BOOT_SCRIPT not in (process / 'cmdline').read_bytes().split(b'\0'):
        continue
      for descriptor in (process / 'fd').iterdir():
        try:
          if str(descriptor.readlink()) in names:
            yield descriptor
        except FileNotFoundError:
          pass
    except (FileNotFoundError, ProcessLookupError):
      pass
    except PermissionError:
      # Other users' processes need not expose their command lines.
      continue


@contextmanager
def panel_owner(hud=False, directory=DIRECTORY, boot_usb_lock=BOOT_USB_LOCK):
  import fcntl
  directory.mkdir(mode=0o700, parents=True, exist_ok=True)
  request = directory / 'hud-request'
  lock = (directory / 'owner.lock').open('a')
  acquired = False
  boot_guard = None
  try:
    identity = process_identity(os.getpid()) if hud else None

    def request_handoff():
      directory.mkdir(mode=0o700, parents=True, exist_ok=True)
      temporary = request.with_suffix('.new')
      temporary.write_text(json.dumps({'pid': os.getpid(), 'identity': identity}))
      temporary.replace(request)

    if hud:
      request_handoff()
    while True:
      try:
        if not hud and hud_requested(directory):
          break
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        break
      except BlockingIOError:
        if not hud:
          break
        request_handoff()
        time.sleep(.1)
    if hud and acquired and boot_usb_lock.parent.is_dir():
      # Protected images keep a separate boot renderer in the read-only OS.
      # Its USB calls use this lock. Hold it for the entire HUD lifetime so
      # logind removing the legacy /dev/shm lock cannot create two USB owners.
      # The HUD driver itself does not acquire this boot-only bus lock.
      with ExitStack() as old_owners:
        current = os.fstat(lock.fileno())
        seen = {(current.st_dev, current.st_ino)}
        for descriptor in legacy_boot_owner_paths(directory):
          try:
            old = old_owners.enter_context(descriptor.open('a'))
          except FileNotFoundError:
            continue
          stat = os.fstat(old.fileno())
          key = (stat.st_dev, stat.st_ino)
          if key in seen:
            continue
          seen.add(key)
          # The boot renderer must finish closing USB before we reserve its
          # per-call bus lock; otherwise a deleted owner inode allows a race.
          while True:
            request_handoff()
            try:
              fcntl.flock(old, fcntl.LOCK_EX | fcntl.LOCK_NB)
              break
            except BlockingIOError:
              time.sleep(.1)
        boot_guard = boot_usb_lock.open('a')
        fcntl.flock(boot_guard, fcntl.LOCK_EX)
    yield acquired
  finally:
    if hud:
      request.unlink(missing_ok=True)
    if acquired:
      fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    if boot_guard is not None:
      fcntl.flock(boot_guard, fcntl.LOCK_UN)
      boot_guard.close()
