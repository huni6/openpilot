"""Display-only snapshots and bounded artwork packets, independent of inference."""
from dataclasses import fields, is_dataclass, replace
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
CLUSTER = ROOT / 'selfdrive/carrot/cluster'
sys.path.insert(0, str(CLUSTER))
import cluster_models as models

HUD_CAPABILITY = 'hl_cluster_v1'
ART_CAPABILITY = 'hl_cluster_art_v1'
HUD_MESSAGE, ART_MESSAGE = 0x4000, 0x4101
MAX_HUD_BYTES, MAX_ART_BYTES = 96 * 1024, 512 * 1024
ART_CHUNK_BYTES = 16 * 1024
MAX_AGE = .5
HEADER = struct.Struct('<d')
ART_HEADER = struct.Struct('<32sII')
HUD_PACKET = Path('/dev/shm/hl-cluster-packet')
HUD_ART = Path('/dev/shm/hl-cluster-art')
HUD_STATUS = Path('/dev/shm/hl-cluster-status.json')
PARAM_DEFAULTS = {'ClusterHud': 1, 'ClusterHudTheme': 0, 'ClusterHudScreenMode': 0,
                  'ClusterHudCameraViewMode': 0, 'ClusterHudRadarInfo': 4,
                  'ClusterHudRadarDisplay': 0, 'ClusterHudRadarSourceColor': 0,
                  'ClusterHudDebug': 0, 'IsOnroad': 0}
TYPES = {name: value for name, value in vars(models).items() if isinstance(value, type) and is_dataclass(value)}


def atomic_write(path, payload):
  temporary = path.with_suffix('.tmp')
  temporary.write_bytes(payload)
  os.replace(temporary, path)


def encode_value(value):
  if is_dataclass(value):
    return {'_type': type(value).__name__, **{f.name: encode_value(getattr(value, f.name)) for f in fields(value)}}
  if isinstance(value, (list, tuple)):
    return [encode_value(v) for v in value]
  if value is None or type(value) in (str, int, bool):
    return value
  if type(value) is float and math.isfinite(value):
    return value
  raise ValueError('Unsupported display value')


def decode_value(value, depth=0):
  if depth > 16:
    raise ValueError('Display data is too deeply nested')
  if isinstance(value, dict):
    cls = TYPES.get(value.get('_type'))
    if cls is None or set(value) != {'_type'} | {f.name for f in fields(cls)}:
      raise ValueError('Unknown display schema')
    return cls(**{k: decode_value(v, depth + 1) for k, v in value.items() if k != '_type'})
  if isinstance(value, list):
    return tuple(decode_value(v, depth + 1) for v in value)
  if value is None or type(value) in (str, int, bool):
    return value
  if type(value) is float and math.isfinite(value):
    return value
  raise ValueError('Invalid display value')


def encode_snapshot(state, params, wall_time):
  # Route video and navigation debug bitmaps have no place in this small channel.
  state = replace(state, route_overlay=None, navi_debug=None)
  artwork = b''
  if state.phone_media is not None:
    artwork = state.phone_media.art_base64.encode('ascii')
    if len(artwork) > MAX_ART_BYTES:
      artwork = b''
    state = replace(state, phone_media=replace(state.phone_media, art_base64=''))
  key = hashlib.sha256(artwork).hexdigest() if artwork else ''
  payload = json.dumps({'version': 1, 'state': encode_value(state), 'params': params,
                        'wall_time': wall_time, 'art_key': key}, ensure_ascii=False, allow_nan=False,
                       separators=(',', ':')).encode('utf-8')
  if len(payload) > MAX_HUD_BYTES - HEADER.size:
    raise ValueError('Display snapshot exceeds transfer budget')
  return payload, artwork


def decode_snapshot(packet):
  if not HEADER.size < len(packet) <= MAX_HUD_BYTES:
    raise ValueError('Invalid display packet size')
  age, = HEADER.unpack_from(packet)
  if not math.isfinite(age) or not 0 <= age <= MAX_AGE:
    raise ValueError('Expired display snapshot')
  doc = json.loads(packet[HEADER.size:])
  if doc.get('version') != 1 or not isinstance(doc.get('params'), dict):
    raise ValueError('Unsupported display protocol')
  if not isinstance(doc.get('wall_time'), (float, int)) or not math.isfinite(doc['wall_time']):
    raise ValueError('Invalid display clock')
  params = doc['params']
  if set(params) != set(PARAM_DEFAULTS) or any(type(v) not in (int, bool) for v in params.values()):
    raise ValueError('Invalid display settings')
  state = decode_value(doc['state'])
  if not isinstance(state, models.ClusterUiState):
    raise ValueError('Missing display state')
  key = doc['art_key']
  if not isinstance(key, str) or key and (len(key) != 64 or any(c not in '0123456789abcdef' for c in key)):
    raise ValueError('Invalid artwork identity')
  return state, params, float(doc['wall_time']), age, key


class ArtAssembler:
  def __init__(self, path=HUD_ART):
    self.path = path
    self.key = None
    self.total = 0
    self.data = bytearray()

  def receive(self, packet):
    if not ART_HEADER.size < len(packet) <= ART_HEADER.size + ART_CHUNK_BYTES:
      return False
    key, offset, total = ART_HEADER.unpack_from(packet)
    fragment = packet[ART_HEADER.size:]
    if not 0 < total <= MAX_ART_BYTES or offset + len(fragment) > total:
      return False
    if offset == 0:
      self.key, self.total, self.data = key, total, bytearray()
    if key != self.key or total != self.total or offset != len(self.data):
      return False
    self.data.extend(fragment)
    if len(self.data) != total:
      return False
    if hashlib.sha256(self.data).digest() != key:
      self.data.clear()
      return False
    atomic_write(self.path, self.data)
    return True


class Publisher:
  def __init__(self, art=True):
    self.directory = tempfile.TemporaryDirectory(prefix='hl-cluster-', dir='/dev/shm')
    self.path = Path(self.directory.name)
    self.process = self._start()
    self.next_restart = 0.
    self.art_enabled = art
    self.last_snapshot = None
    self.artwork = b''
    self.art_key = b''
    self.offset = 0
    self.next_art = 0.
    self.last_art_stamp = None

  def packet(self):
    if self.process.poll() is not None and time.monotonic() >= self.next_restart:
      logging.getLogger('carrot.jetlink').warning('Restarting failed cluster snapshot producer')
      self.next_restart = time.monotonic() + 5
      self.process = self._start()
    try:
      raw = (self.path / 'snapshot').read_bytes()
    except FileNotFoundError:
      return None
    stamp, = HEADER.unpack_from(raw)
    age = time.monotonic() - stamp
    if stamp == self.last_snapshot or not 0 <= age <= MAX_AGE:
      return None
    self.last_snapshot = stamp
    return HEADER.pack(age) + raw[HEADER.size:]

  def _start(self):
    # Do not spend interpreter startup on jetlinkd's inherited FIFO priority.
    return subprocess.Popen(['chrt', '--other', '0', sys.executable,
                             str(Path(__file__).with_name('hud_publisher.py')), str(self.path), str(os.getpid())])

  def media_packet(self):
    if not self.art_enabled:
      return None
    try:
      file = self.path / 'art'
      stamp = file.stat().st_mtime_ns
      if stamp != self.last_art_stamp:
        self.artwork = file.read_bytes()
        self.last_art_stamp = stamp
        self.art_key = hashlib.sha256(self.artwork).digest()
        self.offset, self.next_art = 0, 0.
    except FileNotFoundError:
      return None
    if not self.artwork or len(self.artwork) > MAX_ART_BYTES or time.monotonic() < self.next_art:
      return None
    end = min(self.offset + ART_CHUNK_BYTES, len(self.artwork))
    packet = ART_HEADER.pack(self.art_key, self.offset, len(self.artwork)) + self.artwork[self.offset:end]
    self.offset = end
    if end == len(self.artwork):
      self.offset, self.next_art = 0, time.monotonic() + 5
    return packet

  def close(self):
    if self.process.poll() is None:
      self.process.terminate()
      try:
        self.process.wait(timeout=2)
      except subprocess.TimeoutExpired:
        self.process.kill()
        self.process.wait()
    self.directory.cleanup()
