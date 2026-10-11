from dataclasses import replace
import ast
import hashlib
import json
import re
import socket
import threading
from types import SimpleNamespace

import pytest

import hud
import hud_protocol as wire
from cluster_live import standby_state
from cluster_models import PhoneMediaInfo, ModelPathPoint


def snapshot(**changes):
  state = replace(standby_state(), speed_kph=87., cruise_kph=100, left_blindspot=True,
                  model_path=(ModelPathPoint(10., 1.),),
                  phone_media=PhoneMediaInfo(title='이등병의 편지', artist='김광석', is_playing=True,
                                            duration_ms=200000, position_ms=1000, received_at_ms=99000,
                                            art_base64='YWJj', art_mime='image/jpeg'), **changes)
  params = {**wire.PARAM_DEFAULTS, 'IsOnroad': 1}
  body, art = wire.encode_snapshot(state, params, 100.)
  return state, wire.HEADER.pack(.02) + body, art


def test_nested_unicode_state_roundtrip_without_art_on_snapshot():
  original, packet, art = snapshot()
  restored, params, wall, age, key = wire.decode_snapshot(packet)
  assert restored == replace(original, phone_media=replace(original.phone_media, art_base64=''))
  assert params['IsOnroad'] == 1 and wall == 100 and age == .02
  assert hashlib.sha256(art).hexdigest() == key
  assert len(packet) < wire.MAX_HUD_BYTES


@pytest.mark.parametrize('age', [-1., 1., float('nan'), float('inf')])
def test_reject_stale_nonfinite_age(age):
  _, packet, _ = snapshot()
  with pytest.raises(ValueError):
    wire.decode_snapshot(wire.HEADER.pack(age) + packet[8:])


def test_reject_unknown_schema_and_oversized_data():
  _, packet, _ = snapshot()
  doc = json.loads(packet[8:])
  doc['state']['_type'] = '__import__'
  with pytest.raises(ValueError):
    wire.decode_snapshot(packet[:8] + json.dumps(doc).encode())
  with pytest.raises(ValueError):
    wire.decode_snapshot(b'x' * (wire.MAX_HUD_BYTES + 1))


def chunks(data):
  key = hashlib.sha256(data).digest()
  return [wire.ART_HEADER.pack(key, offset, len(data)) + data[offset:offset + wire.ART_CHUNK_BYTES]
          for offset in range(0, len(data), wire.ART_CHUNK_BYTES)]


def test_art_fragments_atomic_and_integrity_checked(tmp_path):
  path = tmp_path / 'art'
  receiver = wire.ArtAssembler(path)
  data = b'a' * 40000
  packets = chunks(data)
  assert not receiver.receive(packets[1])
  assert not path.exists()
  assert not receiver.receive(packets[0])
  assert not receiver.receive(packets[1])
  assert receiver.receive(packets[2])
  assert path.read_bytes() == data
  damaged = chunks(b'bad')[0][:-1] + b'x'
  assert not receiver.receive(damaged)
  assert path.read_bytes() == data
  assert not receiver.receive(wire.ART_HEADER.pack(bytes(32), 0, wire.MAX_ART_BYTES + 1) + b'x')


def test_remote_clock_playback_cover_staleness_and_track_switch(tmp_path, monkeypatch):
  tick = [10.]
  monkeypatch.setattr(hud.time, 'monotonic', lambda: tick[0])
  monkeypatch.setattr(hud.time, 'time', lambda: 500000.)
  remote = hud.RemoteState(tmp_path / 'packet', tmp_path / 'art')
  state, packet, art = snapshot()
  remote.packet_path.write_bytes(wire.HEADER.pack(10.) + packet)
  remote.art_path.write_bytes(art)
  result = remote.current()
  assert result.phone_media.title == state.phone_media.title
  assert result.phone_media.position_ms == 2020
  assert result.phone_media.received_at_ms == 500000000
  assert result.phone_media.art_base64 == 'YWJj'
  assert remote.wall_time() == 100.02
  tick[0] += .6
  assert remote.current() == standby_state()
  tick[0] = 10.1
  next_state = replace(state, phone_media=replace(state.phone_media, title='Next', art_base64='eHl6'))
  body, _ = wire.encode_snapshot(next_state, wire.PARAM_DEFAULTS, 100.1)
  remote.packet_path.write_bytes(wire.HEADER.pack(10.1) + wire.HEADER.pack(0.) + body)
  assert remote.current().phone_media.art_base64 == ''
  remote.packet_path.write_bytes(b'bad')
  assert remote.current() == standby_state()


def test_display_params_theme_brightness_and_disconnect(tmp_path, monkeypatch):
  remote = hud.RemoteState(tmp_path / 'packet', tmp_path / 'art')
  monkeypatch.setattr(hud, 'REMOTE', remote)
  state, _, _ = snapshot()
  for mode, expected in ((1, 20), (2, 60), (0, 60)):
    body, _ = wire.encode_snapshot(state, {**wire.PARAM_DEFAULTS, 'ClusterHudTheme': mode, 'IsOnroad': 1}, 3 * 3600.)
    remote.packet_path.write_bytes(wire.HEADER.pack(hud.time.monotonic()) + wire.HEADER.pack(0.) + body)
    params = hud.DisplayParams()
    assert hud.config.cluster_theme_brightness_percent(params.get_int('ClusterHudTheme')) == expected
    assert params.get_bool('IsOnroad')
  remote.packet_path.unlink()
  assert not params.get_bool('IsOnroad')


def test_publisher_bounded_art_and_old_snapshot(tmp_path, monkeypatch):
  publisher = object.__new__(wire.Publisher)
  publisher.path = tmp_path
  publisher.process = SimpleNamespace(poll=lambda: None)
  publisher.art_enabled = True
  publisher.artwork, publisher.art_key = b'', b''
  publisher.last_art_stamp = publisher.last_snapshot = None
  publisher.offset = publisher.next_art = 0
  monkeypatch.setattr(wire.time, 'monotonic', lambda: 10.)
  (tmp_path / 'art').write_bytes(b'a' * 40000)
  receiver = wire.ArtAssembler(tmp_path / 'result')
  for _ in range(3):
    packet = publisher.media_packet()
    assert len(packet) <= wire.ART_HEADER.size + wire.ART_CHUNK_BYTES
    receiver.receive(packet)
  assert publisher.media_packet() is None
  assert (tmp_path / 'result').read_bytes() == b'a' * 40000
  _, packet, _ = snapshot()
  (tmp_path / 'snapshot').write_bytes(wire.HEADER.pack(9.) + packet[8:])
  assert publisher.packet() is None


def test_display_traffic_does_not_precede_inference_reply():
  from openpilot.selfdrive.modeld.jetlink import daemon
  import inspect
  body = inspect.getsource(daemon._serve_local)
  start = body.index('seq = client.infer_begin')
  end = body.index('replied = time.monotonic()', start)
  assert 'publish_hud(' not in body[start:end]
  assert 'publish_hud(' in body[end:]


def test_upstream_boot_updater_cannot_replace_custom_runtime():
  from openpilot.selfdrive.modeld.jetlink import startup
  with pytest.raises(RuntimeError, match='cluster_jetson'):
    startup.wait_for_boot_update(None, {'carrot_host': 'jetson', startup.CAPABILITY: True}, None, None, None)


def test_transport_display_exception_does_not_stop_inference(monkeypatch):
  import server
  session = object.__new__(server.ClusterSession)
  session.last_seq = 0
  monkeypatch.setattr(server, 'atomic_write', lambda *a: (_ for _ in ()).throw(OSError('no disk')))
  session.handle(SimpleNamespace(msg_type=wire.HUD_MESSAGE, seq=1, payload=b'a' * 100))
  assert session.last_seq == 1


def test_framed_transport_delivers_unicode_and_art_without_losing_ping(tmp_path, monkeypatch):
  import server
  from jetlink import protocol as protocol
  from jetlink.client import JetlinkClient
  from jetlink.transport.tcp import TcpTransport

  remote = hud.RemoteState(tmp_path / 'packet', tmp_path / 'art')
  monkeypatch.setattr(server, 'HUD_PACKET', remote.packet_path)
  host = SimpleNamespace(lock=threading.Lock(), session=None, telemetry=SimpleNamespace(read=dict),
                         backend=SimpleNamespace(describe=lambda: {'backend': 'test', 'device': 'test'}),
                         status=lambda *args: {'state': 'idle'}, loaded_sha=lambda: None,
                         cache=SimpleNamespace(inventory=list), sleep_after=0)
  ours, peer = socket.socketpair()
  client = JetlinkClient(TcpTransport(ours))
  transport = TcpTransport(peer)
  session = server.ClusterSession(transport, host)
  session.art = wire.ArtAssembler(remote.art_path)
  host.session = session

  def send(kind, payload=b''):
    seq = client._next_seq()
    client.t.send(kind, seq, [payload] if payload else ())
    session.handle(session.t.recv(timeout=1))
    return seq

  try:
    seq = send(protocol.Msg.HELLO_REQ, b'{}')
    hello = client.t.recv(timeout=1)
    capabilities = json.loads(bytes(hello.payload))
    assert hello.seq == seq and hello.msg_type == protocol.Msg.HELLO_RESP
    assert capabilities[wire.HUD_CAPABILITY] and capabilities[wire.ART_CAPABILITY]
    state, _, _ = snapshot()
    state = replace(state, phone_media=replace(state.phone_media, art_base64='YWJj' * 12000))
    body, art = wire.encode_snapshot(state, {**wire.PARAM_DEFAULTS, 'IsOnroad': 1}, 100.)
    packet = wire.HEADER.pack(0.) + body
    send(wire.HUD_MESSAGE, packet)
    for fragment in chunks(art):
      send(wire.ART_MESSAGE, fragment)
    seq = send(protocol.Msg.PING)
    pong = client.t.recv(timeout=1)
    assert pong.seq == seq and pong.msg_type == protocol.Msg.PONG
    result = remote.current()
    assert result.phone_media.title == '이등병의 편지'
    assert result.phone_media.artist == '김광석'
    assert result.phone_media.art_base64.encode() == art
    assert result.speed_kph == 87 and result.left_blindspot
  finally:
    session.close()
    client.close()
    transport.close()
  assert not remote.packet_path.exists()
  assert remote.current() == standby_state()


def test_bridge_only_uses_params_registered_in_this_older_branch():
  registered = set(re.findall(r'\{"([A-Za-z0-9_]+)"', (wire.ROOT / 'common/params_keys.h').read_text()))
  assert set(wire.PARAM_DEFAULTS) <= registered
  source = wire.ROOT / 'selfdrive/modeld/jetlink/daemon.py'
  for call in ast.walk(ast.parse(source.read_text())):
    if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        and call.func.attr in ('get_bool', 'get_int', 'put_bool_nonblocking') and call.args):
      assert isinstance(call.args[0], ast.Constant) and call.args[0].value in registered
