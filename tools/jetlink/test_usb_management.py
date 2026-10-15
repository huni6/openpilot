import errno
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

import manage_jetson as client
import setup_comma_remote as remote
import usb_management as usb


@pytest.fixture
def gadget(tmp_path):
  root = tmp_path / 'gadget'
  net = tmp_path / 'net'
  (root / 'configs/c.1').mkdir(parents=True)
  (root / 'functions/ffs.jetlink').mkdir(parents=True)
  (root / 'configs/c.1/ffs.jetlink').symlink_to(root / 'functions/ffs.jetlink')
  (root / 'functions' / usb.FUNCTION).mkdir()
  (root / 'functions' / usb.FUNCTION / 'ifname').write_text('usb0\n')
  (net / 'usb0').mkdir(parents=True)
  (net / 'usb0/address').write_text(usb.COMMA_MAC)
  (net / 'usb0/carrier').write_text('1')
  for name, value in {'UDC': '', 'bDeviceClass': '0x00', 'bDeviceSubClass': '0x00',
                      'bDeviceProtocol': '0x00', 'bcdDevice': '0x0100'}.items():
    (root / name).write_text(value + '\n')
  return root, net


def test_usb_keeps_functionfs_and_adds_only_management_address(gadget):
  root, net = gadget
  calls = []
  assert usb.configure(root, net, lambda args: calls.append(args)) == usb.FUNCTION
  assert calls == []
  (root / 'UDC').write_text('controller')
  assert usb.activate(root, net, lambda args: calls.append(args)) == 'usb0'
  assert (root / 'configs/c.1/ffs.jetlink').is_symlink()
  assert (root / 'configs/c.1' / usb.FUNCTION).is_symlink()
  assert ['ip', 'address', 'replace', usb.COMMA_IP + '/30', 'dev', 'usb0'] in calls
  assert not any('route' in call or 'default' in call for call in calls)
  assert (root / 'UDC').read_text().strip() == 'controller'


def test_bound_gadget_is_never_modified(gadget):
  root, net = gadget
  (root / 'UDC').write_text('controller')
  with pytest.raises(ValueError, match='already bound'):
    usb.configure(root, net, lambda args: pytest.fail('no network modification'))
  assert not (root / 'configs/c.1' / usb.FUNCTION).exists()


def test_network_activation_requires_bound_udc(gadget):
  root, net = gadget
  usb.configure(root, net)
  with pytest.raises(ValueError, match='Bind FunctionFS'):
    usb.activate(root, net, lambda args: pytest.fail('no network modification'))


def test_old_agnos_does_not_access_netdev_before_udc_bind(gadget, monkeypatch):
  root, net = gadget
  original = Path.write_text
  def old_kernel(path, value, *args, **kwargs):
    if path.name in ('dev_addr', 'host_addr'):
      raise OSError(errno.ENODEV, 'No such device')
    return original(path, value, *args, **kwargs)
  monkeypatch.setattr(Path, 'write_text', old_kernel)
  monkeypatch.setattr(usb, 'netdev', lambda *args: pytest.fail('pre-bind netdev access'))
  assert usb.configure(root, net, lambda args: pytest.fail('pre-bind network changes')) == usb.FUNCTION
  assert (root / 'configs/c.1' / usb.FUNCTION).is_symlink()


def test_mobile_mode_cannot_share_management_function(gadget):
  root, net = gadget
  (root / 'configs/c.1/ncm.jetlink').symlink_to(root / 'functions/ncm.jetlink')
  with pytest.raises(ValueError, match='must not be combined'):
    usb.configure(root, net)


def test_foreign_interface_is_never_reconfigured(gadget):
  root, net = gadget
  usb.configure(root, net)
  (root / 'UDC').write_text('controller')
  (net / 'usb0/address').write_text('11:22:33:44:55:66')
  with pytest.raises(ValueError, match='MAC'):
    usb.activate(root, net, lambda args: pytest.fail('no network modification'))
  assert (root / 'configs/c.1' / usb.FUNCTION).is_symlink()


def test_network_failure_does_not_unbind_live_bulk(gadget):
  root, net = gadget
  usb.configure(root, net)
  (root / 'UDC').write_text('controller')
  def command(args):
    raise subprocess.CalledProcessError(1, args)
  with pytest.raises(subprocess.CalledProcessError):
    usb.activate(root, net, command)
  assert (root / 'UDC').read_text() == 'controller'
  assert (root / 'configs/c.1/ffs.jetlink').is_symlink()
  assert (root / 'configs/c.1' / usb.FUNCTION).is_symlink()


def test_detach_accepts_destroyed_old_kernel_netdev(gadget, monkeypatch):
  root, net = gadget
  usb.configure(root, net)
  def missing(*args):
    raise OSError(errno.ENODEV, 'No such device')
  monkeypatch.setattr(usb, 'netdev', missing)
  usb.detach(root, net, lambda args: pytest.fail('netdev already destroyed'))
  assert not (root / 'configs/c.1' / usb.FUNCTION).exists()


def test_detach_retains_kernel_function_without_flushing_other_addresses(gadget):
  root, net = gadget
  usb.configure(root, net, lambda args: '')
  calls = []
  def command(args):
    calls.append(args)
    return json.dumps([dict(addr_info=[dict(local=usb.COMMA_IP, prefixlen=30), dict(local='192.168.3.1', prefixlen=24)])])
  usb.detach(root, net, command)
  assert (root / 'functions' / usb.FUNCTION).is_dir()
  assert not (root / 'configs/c.1' / usb.FUNCTION).exists()
  assert calls[-1] == ['ip', 'address', 'del', usb.COMMA_IP + '/30', 'dev', 'usb0']
  usb.detach(root, net, lambda args: pytest.fail('idempotent detach'))


@pytest.mark.parametrize('route', [[], [dict(dev='wlan0', gateway='192.168.1.1', prefsrc=usb.COMMA_IP)],
                                   [dict(dev='../usb0', prefsrc=usb.COMMA_IP)], [dict(dev='usb0', prefsrc='1.2.3.4')]])
def test_usb_candidate_rejects_non_direct_routes(gadget, route):
  _, net = gadget
  assert usb.usb_candidate(net, lambda args: json.dumps(route)) == []


def test_usb_candidate_requires_owned_function_and_link_carrier(gadget):
  root, net = gadget
  usb.configure(root, net)
  (root / 'UDC').write_text('controller')
  (root / 'functions' / usb.FUNCTION / 'dev_addr').write_text('02:12:34:56:78:90\x00')
  (net / 'usb0/address').write_text('02:12:34:56:78:90')
  def command(args):
    return json.dumps([dict(dev='usb0', prefsrc=usb.COMMA_IP)])
  assert usb.usb_candidate(net, command, root) == [usb.JETSON_IP]
  (net / 'usb0/carrier').write_text('0')
  assert usb.usb_candidate(net, command, root) == []


def test_usb_priority_then_changed_wifi_without_stale_linklocal(tmp_path, monkeypatch):
  config = dict(host='192.168.0.4')
  client.private_json(tmp_path / 'last-host.json', dict(host=usb.JETSON_IP))
  monkeypatch.setattr(client, 'usb_candidate', lambda: [usb.JETSON_IP])
  assert client.candidates(config, tmp_path, ['192.168.42.5']) == [usb.JETSON_IP, '192.168.42.5', '192.168.0.4']
  monkeypatch.setattr(client, 'usb_candidate', list)
  assert client.candidates(config, tmp_path, ['192.168.42.5']) == ['192.168.42.5', '192.168.0.4']


def test_usb_updates_use_lan_until_field_verified(tmp_path, monkeypatch):
  calls = []
  def connect(state, allow_usb=True):
    calls.append(allow_usb)
    return usb.JETSON_IP if allow_usb else '192.168.0.4'
  monkeypatch.setattr(client, 'connect', connect)
  assert client.connect_for_update(tmp_path) == '192.168.0.4'
  assert calls == [True, False]
  client.private_json(tmp_path / 'config.json', dict(usb_updates_verified=True))
  assert client.connect_for_update(tmp_path) == usb.JETSON_IP


def test_unverified_usb_only_update_is_refused(tmp_path, monkeypatch):
  def connect(state, allow_usb=True):
    if not allow_usb:
      raise ConnectionError('No LAN')
    return usb.JETSON_IP
  monkeypatch.setattr(client, 'connect', connect)
  with pytest.raises(ConnectionError, match='not field-verified'):
    client.connect_for_update(tmp_path)


@pytest.mark.parametrize('address', ['169.254.77.0', '169.254.77.2', '169.254.77.3', '169.254.1.1'])
def test_only_exact_usb_peer_is_allowed(address):
  assert client.address(usb.JETSON_IP) == usb.JETSON_IP
  with pytest.raises(ValueError):
    client.address(address)


def host_root(tmp_path):
  for name in ('etc/NetworkManager/system-connections', 'usr/lib/carrot-jetlink-storage', 'run'):
    (tmp_path / name).mkdir(parents=True)
  (tmp_path / 'etc/carrot-jetlink-protected.json').write_text('{}')
  (tmp_path / 'run/carrot-storage.json').write_text('{"state":"protected"}')
  (tmp_path / 'usr/lib/carrot-jetlink-storage/protected_storage.py').touch()
  device = tmp_path / 'sys/devices/usb/1-2/1-2:1.1'
  device.mkdir(parents=True)
  (device / 'driver').symlink_to(tmp_path / 'sys/bus/usb/drivers/cdc_ncm')
  for attr, value in [('idVendor', '1209'), ('idProduct', '0001'), ('bcdDevice', '0102')]:
    (device.parent / attr).write_text(value)
  interface = tmp_path / 'sys/class/net/usb0'
  interface.mkdir(parents=True)
  (interface / 'device').symlink_to(device)
  return tmp_path


def test_host_install_persists_narrow_profile_without_touching_wifi(tmp_path):
  root = host_root(tmp_path)
  wifi = root / 'etc/NetworkManager/system-connections/existing.nmconnection'
  wifi.write_text('original')
  calls = []
  def command(args):
    calls.append(args)
    return 'ID_PATH=platform-xhci-usb-0:2:1.1' if args[0] == 'udevadm' else ''
  result = usb.install_host(root, command)
  profile = wifi.with_name(usb.PROFILE)
  assert result['physical_usb_verified'] is False
  assert profile.read_text() == usb.host_profile('platform-xhci-usb-0:2:1.1')
  assert profile.stat().st_mode & 0o777 == 0o600
  assert 'driver=cdc_ncm' in profile.read_text() and 'mac-address=' not in profile.read_text()
  assert 'never-default=true' in profile.read_text() and 'gateway=' not in profile.read_text()
  assert calls[-1][-1] == 'save' and wifi.read_text() == 'original'
  assert usb.install_host(root, command) == result


def test_host_migrates_exact_legacy_profile(tmp_path):
  root = host_root(tmp_path)
  path = root / 'etc/NetworkManager/system-connections' / usb.PROFILE
  path.write_text(usb.host_profile())
  usb.install_host(root, lambda args: 'ID_PATH=platform-xhci-usb-0:2:1.1' if args[0] == 'udevadm' else '')
  assert path.read_text() == usb.host_profile('platform-xhci-usb-0:2:1.1')


def test_failed_migration_restores_legacy_profile(tmp_path):
  root = host_root(tmp_path)
  path = root / 'etc/NetworkManager/system-connections' / usb.PROFILE
  path.write_text(usb.host_profile())
  saves = []
  def command(args):
    if args[0] == 'udevadm':
      return 'ID_PATH=platform-xhci-usb-0:2:1.1'
    if args[-1] == 'save':
      saves.append(path.read_text())
      if len(saves) == 1:
        raise RuntimeError('partial save')
    return ''
  with pytest.raises(RuntimeError, match='partial save'):
    usb.install_host(root, command)
  assert saves[-1] == path.read_text() == usb.host_profile()


def test_host_profile_requires_management_usb_identity(tmp_path):
  root = host_root(tmp_path)
  (root / 'sys/devices/usb/1-2/bcdDevice').write_text('0100')
  with pytest.raises(ValueError, match='exactly one'):
    usb.install_host(root, lambda args: pytest.fail('no changes for non-management USB'))


def test_failed_host_save_removes_only_new_profile_and_persists_rollback(tmp_path):
  root = host_root(tmp_path)
  calls = []
  def command(args):
    calls.append(args)
    if args[0] == 'udevadm':
      return 'ID_PATH=platform-xhci-usb-0:2:1.1'
    if len(calls) == 3:
      raise RuntimeError('save failure')
  with pytest.raises(RuntimeError, match='save failure'):
    usb.install_host(root, command)
  assert not (root / 'etc/NetworkManager/system-connections' / usb.PROFILE).exists()
  assert calls[-1][-1] == 'save'


def test_different_existing_host_profile_is_preserved(tmp_path):
  root = host_root(tmp_path)
  path = root / 'etc/NetworkManager/system-connections' / usb.PROFILE
  path.write_text('user customized')
  with pytest.raises(ValueError, match='differs'):
    usb.install_host(root, lambda args: 'ID_PATH=platform-xhci-usb-0:2:1.1' if args[0] == 'udevadm' else pytest.fail('no changes'))
  assert path.read_text() == 'user customized'


def test_tailscale_login_does_not_capture_dns_routes_or_enable_ssh_server():
  args = remote.login_args()
  for option in ('--accept-dns=false', '--accept-routes=false', '--ssh=false', '--advertise-routes=',
                 '--exit-node=', '--advertise-exit-node=false'):
    assert option in args
  assert '--auth-key' not in ' '.join(args)
  assert 'hlts0' in remote.unit_text() and '/data/hl-comma-remote/state' in remote.unit_text()


def test_tailscale_unpack_checks_integrity_and_never_extracts_paths(tmp_path):
  data = io.BytesIO()
  with tarfile.open(fileobj=data, mode='w:gz') as archive:
    for name in ('tailscale', 'tailscaled'):
      member = tarfile.TarInfo(f'tailscale_{remote.VERSION}_arm64/{name}')
      member.size = 4
      archive.addfile(member, io.BytesIO(b'test'))
  raw = data.getvalue()
  assert remote.unpack(raw, hashlib.sha256(raw).hexdigest()) == dict(tailscale=b'test', tailscaled=b'test')
  with pytest.raises(ValueError, match='SHA256'):
    remote.unpack(raw)


def test_tailscale_installer_refuses_this_non_comma_host():
  if Path('/TICI').is_file():
    pytest.skip('Do not run installer on a live comma in a unit test')
  with pytest.raises(ValueError, match='AGNOS comma'):
    remote.install()
