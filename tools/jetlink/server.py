"""Pinned TensorRT server with this branch's display-only HUD extension."""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'third_party/jetlink'))
from hud_protocol import (HUD_CAPABILITY, ART_CAPABILITY, HUD_MESSAGE, ART_MESSAGE, MAX_HUD_BYTES,
                          HUD_PACKET, HUD_STATUS, HEADER, ArtAssembler, atomic_write)
from host_reader import ReadAheadTransport
from host_health import health_worker
from wifi_protocol import CAPABILITY as WIFI_CAPABILITY, MESSAGE as WIFI_MESSAGE, receive as receive_wifi
from jetlink import protocol as P
from jetlink.server.session import Session
from jetlink.server import main as server


class DisplayTelemetry:
  def __init__(self, original):
    self.original = original
    self.health = health_worker() if Path('/etc/nv_tegra_release').is_file() else None

  def read(self):
    connected = False
    try:
      value = json.loads(HUD_STATUS.read_text())
      connected = 0 <= time.monotonic() - value['updated'] < 2
    except (OSError, ValueError, KeyError, TypeError):
      pass
    return {**self.original.read(), 'carrot_hud_connected': connected,
            **({'carrot_health': self.health.read()} if self.health else {})}


class ClusterSession(Session):
  def _send(self, msg_type, seq, parts=(), flags=0):
    if msg_type == P.Msg.INFER_RESP and self.telemetry.health is not None:
      status = P.unpack_infer_resp(parts[0])[1]
      if status != P.Status.OK:
        self.telemetry.health.record_error('Inference', f'result status {status}')
    return super()._send(msg_type, seq, parts, flags)

  def _error(self, seq, error, detail=''):
    if self.telemetry.health is not None:
      self.telemetry.health.record_error(error, detail)
    return super()._error(seq, error, detail)

  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    if sys.platform == 'linux':
      self.t = ReadAheadTransport(self.t)
    self.telemetry = DisplayTelemetry(self.telemetry)
    self.art = ArtAssembler()

  def _send_json(self, msg_type, seq, obj, flags=0):
    if msg_type == P.Msg.HELLO_RESP:
      obj = {**obj, HUD_CAPABILITY: True, ART_CAPABILITY: True, WIFI_CAPABILITY: True,
             'carrot_host': 'jetson' if Path('/etc/nv_tegra_release').is_file() else 'unknown',
             'hl_cluster_branch': 'cluster_jetson'}
    return super()._send_json(msg_type, seq, obj, flags)

  def handle(self, msg):
    if msg.msg_type == WIFI_MESSAGE:
      if msg.seq > self.last_seq:
        self.last_seq = msg.seq
        receive_wifi(msg.payload)
      return
    if msg.msg_type not in (HUD_MESSAGE, ART_MESSAGE):
      return super().handle(msg)
    if msg.seq <= self.last_seq:
      return
    self.last_seq = msg.seq
    try:
      if msg.msg_type == ART_MESSAGE:
        self.art.receive(msg.payload)
      elif HEADER.size < len(msg.payload) <= MAX_HUD_BYTES:
        atomic_write(HUD_PACKET, HEADER.pack(time.monotonic()) + msg.payload)
    except (OSError, ValueError):
      pass  # A display failure must never invalidate an inference result.

  def close(self):
    try:
      HUD_PACKET.unlink(missing_ok=True)
    except OSError:
      pass
    if isinstance(self.t, ReadAheadTransport):
      self.t.close()
    super().close()


if __name__ == '__main__':
  server.Session = ClusterSession
  raise SystemExit(server.main())
