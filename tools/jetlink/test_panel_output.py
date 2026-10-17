"""Hardware-free checks for native panel geometry and legacy boot ownership."""
from contextlib import nullcontext
import fcntl
import sys
from types import SimpleNamespace

import pytest

import display_owner
import hud


@pytest.fixture(autouse=True)
def process_identity_on_development_hosts(monkeypatch):
  if not sys.platform.startswith('linux'):
    monkeypatch.setattr(display_owner, 'process_identity', lambda pid: ['test-boot', str(pid)])


def run_hud(monkeypatch, tmp_path, extra=(), capture=None):
  sent, calls = [], []

  class Display:
    _frame_error_count = 0

    def send_jpeg(self, frame):
      sent.append(frame)

  module = SimpleNamespace(TuringUsbDisplay=Display)

  def main():
    calls.append(list(sys.argv))
    display = Display()
    display.send_jpeg(b'first JPEG')
    display.send_jpeg(b'second JPEG')

  module.main = main
  monkeypatch.setitem(sys.modules, 'main', module)
  monkeypatch.setitem(sys.modules, 'cluster_renderer', SimpleNamespace())
  # main installs a Params shim; keep it isolated from other tests.
  monkeypatch.setitem(sys.modules, 'openpilot.common.params', SimpleNamespace())
  monkeypatch.setattr(sys, 'argv', ['hud.py', *extra])
  monkeypatch.setattr(hud, 'REMOTE', SimpleNamespace(refresh=lambda: True,
                                                  params={'ClusterHud': 1, 'IsOnroad': 1}))
  monkeypatch.setattr(hud, 'HUD_STATUS', tmp_path / 'status.json')
  monkeypatch.setattr(display_owner, 'panel_owner', lambda **kwargs: nullcontext(True))
  if capture is None:
    monkeypatch.delenv('CLUSTER_JPEG_CAPTURE', raising=False)
  else:
    monkeypatch.setenv('CLUSTER_JPEG_CAPTURE', str(capture))
  hud.main()
  return calls[0], sent


def test_native_panel_dimensions_are_not_overridden(monkeypatch, tmp_path):
  args, sent = run_hud(monkeypatch, tmp_path)
  assert '--width' not in args and '--height' not in args
  assert args[args.index('--usb-codec') + 1] == 'jpeg'
  assert args[args.index('--fps') + 1] == '10'
  assert len(sent) == 2


def test_explicit_diagnostic_dimensions_still_work(monkeypatch, tmp_path):
  args, _ = run_hud(monkeypatch, tmp_path, ['--width', '1920', '--height', '480'])
  assert args[-4:] == ['--width', '1920', '--height', '480']


def test_capture_is_exactly_one_transmitted_frame(monkeypatch, tmp_path):
  path = tmp_path / 'frame.jpg'
  _, sent = run_hud(monkeypatch, tmp_path, capture=path)
  assert path.read_bytes() == sent[0]
  assert not path.with_suffix('.tmp').exists()


def test_capture_error_does_not_stop_display(monkeypatch, tmp_path, capsys):
  _, sent = run_hud(monkeypatch, tmp_path, capture=tmp_path / 'missing/frame.jpg')
  assert len(sent) == 2
  assert capsys.readouterr().out.count('capture failed') == 1
  assert (tmp_path / 'status.json').is_file()


def test_boot_usb_guard_survives_legacy_lock_removal(tmp_path):
  legacy = tmp_path / 'shm'
  boot_lock = tmp_path / 'run/usb.lock'
  boot_lock.parent.mkdir()
  with display_owner.panel_owner(hud=True, directory=legacy, boot_usb_lock=boot_lock) as acquired:
    assert acquired
    # Model logind cleanup while the HUD still holds the now-unlinked inode.
    (legacy / 'owner.lock').unlink()
    legacy.rmdir()
    with display_owner.panel_owner(directory=legacy, boot_usb_lock=boot_lock) as boot_owner:
      assert boot_owner  # The old ownership file alone no longer protects USB.
      with boot_lock.open('a') as attempted_usb:
        with pytest.raises(BlockingIOError):
          fcntl.flock(attempted_usb, fcntl.LOCK_EX | fcntl.LOCK_NB)
  with boot_lock.open('a') as attempted_usb:
    fcntl.flock(attempted_usb, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_legacy_images_without_boot_service_still_work(tmp_path):
  with display_owner.panel_owner(hud=True, directory=tmp_path / 'shm',
                                boot_usb_lock=tmp_path / 'missing/usb.lock') as acquired:
    assert acquired


def test_exception_releases_both_locks(tmp_path):
  legacy = tmp_path / 'shm'
  boot_lock = tmp_path / 'run/usb.lock'
  boot_lock.parent.mkdir()
  with pytest.raises(RuntimeError):
    with display_owner.panel_owner(hud=True, directory=legacy, boot_usb_lock=boot_lock):
      raise RuntimeError('renderer failed')
  with display_owner.panel_owner(hud=True, directory=legacy, boot_usb_lock=boot_lock) as acquired:
    assert acquired
