"""Run the unchanged fix5 ambient cluster on the Jetson's TURZX USB port."""
from dataclasses import replace
import hashlib
import json
import math
import struct
import sys
import time
import types

from hud_protocol import (ROOT, HEADER, HUD_PACKET, HUD_ART, HUD_STATUS, MAX_AGE,
                          MAX_HUD_BYTES, MAX_ART_BYTES, PARAM_DEFAULTS, decode_snapshot, atomic_write)
from cluster_live import standby_state
from cluster_layout import extrapolated_media_position_ms
import cluster_config as config


class RemoteState:
  def __init__(self, packet_path=HUD_PACKET, art_path=HUD_ART):
    self.packet_path, self.art_path = packet_path, art_path
    self.stamp = None
    self.state = standby_state()
    self.params = dict(PARAM_DEFAULTS)
    self.arrived = self.source_age = 0.
    self.wall = time.time()  # noqa: TID251 -- calendar clock, never a deadline
    self.key = ''
    self.art_stamp = None
    self.art_key = self.artwork = ''
    self.error = 'Waiting for comma'

  def refresh(self):
    try:
      stat = self.packet_path.stat()
      if stat.st_size > MAX_HUD_BYTES + HEADER.size:
        raise ValueError('Oversized display file')
      if stat.st_mtime_ns != self.stamp:
        packet = self.packet_path.read_bytes()
        arrived, = HEADER.unpack_from(packet)
        if not math.isfinite(arrived) or not 0 <= time.monotonic() - arrived <= MAX_AGE:
          raise ValueError('Expired display file')
        state, params, wall, age, key = decode_snapshot(packet[HEADER.size:])
        self.state, self.params, self.wall, self.source_age, self.key = state, params, wall, age, key
        self.arrived, self.stamp = arrived, stat.st_mtime_ns
        self.error = ''
      return self.age() <= MAX_AGE
    except (OSError, ValueError, KeyError, TypeError, OverflowError, struct.error) as exc:
      self.error = str(exc)
      return False

  def age(self):
    return self.source_age + max(0., time.monotonic() - self.arrived)

  def wall_time(self):
    return self.wall + self.age() if self.arrived else time.time()  # noqa: TID251

  def media_art(self):
    if not self.key:
      return ''
    try:
      stat = self.art_path.stat()
      if stat.st_size > MAX_ART_BYTES:
        return ''
      if stat.st_mtime_ns != self.art_stamp:
        data = self.art_path.read_bytes()
        self.artwork = data.decode('ascii')
        self.art_key = hashlib.sha256(data).hexdigest()
        self.art_stamp = stat.st_mtime_ns
    except (OSError, UnicodeError):
      return ''
    return self.artwork if self.key == self.art_key else ''

  def current(self):
    if not self.refresh():
      return standby_state()
    state = self.state
    media = state.phone_media
    if media is not None:
      # Phone timestamps belong to the comma clock, not the Jetson RTC.
      position = extrapolated_media_position_ms(media.position_ms, media.duration_ms, media.is_playing,
                                               media.received_at_ms or media.updated_at_ms,
                                               int(self.wall_time() * 1000))
      media = replace(media, position_ms=position, received_at_ms=int(time.time() * 1000),  # noqa: TID251 -- renderer wall-clock contract
                      art_base64=self.media_art(), art_hash=self.key)
      state = replace(state, phone_media=media)
    return state


REMOTE = RemoteState()


class DisplayParams:
  """Read-only display settings. No car-control Params are available on Jetson."""
  def get_int(self, key):
    fresh = REMOTE.refresh()
    if key not in PARAM_DEFAULTS:
      return 0
    if key in ('IsOnroad', 'ClusterHudDebug') and not fresh:
      return 0
    value = REMOTE.params[key]
    if key == 'ClusterHudTheme':
      return 1 if config.current_cluster_theme(value, now=REMOTE.wall_time()).is_dark else 2
    return value

  def get_bool(self, key):
    return bool(self.get_int(key))

  def get(self, key, encoding=None):
    value = str(self.get_int(key))
    return value if encoding else value.encode()


class RemoteLiveSource:
  def __init__(self, **kwargs):
    pass

  def update(self):
    return REMOTE.current()

  def status_text(self):
    return f'Jetson display age={REMOTE.age():.3f}s' if REMOTE.refresh() else 'Comma display disconnected'

  def set_profile_enabled(self, enabled):
    pass

  def set_debug_panels_enabled(self, **kwargs):
    pass

  def set_hud_debug_mode(self, mode):
    pass

  def profile_samples(self):
    return ()

  def close(self):
    pass


def main():
  if '--help' not in sys.argv:
    while not (REMOTE.refresh() and REMOTE.params['ClusterHud'] in (1, 2)
               and (REMOTE.params['IsOnroad'] or REMOTE.params['ClusterHudDebug'])):
      time.sleep(.25)
  mode = REMOTE.params['ClusterHud']
  sys.path.insert(0, str(ROOT))
  # Only this host process replaces the native Params/live-input dependencies.
  params_module = types.ModuleType('openpilot.common.params')
  params_module.Params = DisplayParams
  sys.modules['openpilot.common.params'] = params_module
  import main as cluster
  import cluster_renderer
  cluster.OpenpilotLiveSource = RemoteLiveSource
  cluster_renderer.kst_clock_text_12h = lambda **kw: config.kst_clock_text_12h(now=REMOTE.wall_time(), **kw)
  cluster_renderer.kst_clock_text = lambda **kw: config.kst_clock_text(now=REMOTE.wall_time(), **kw)
  cluster.kst_clock_text = lambda **kw: config.kst_clock_text(now=REMOTE.wall_time(), **kw)

  original = cluster.TuringUsbDisplay.send_jpeg

  def send_jpeg(display, frame):
    original(display, frame)
    if REMOTE.refresh() and display._frame_error_count == 0:
      atomic_write(HUD_STATUS, json.dumps({'updated': time.monotonic()}).encode())

  cluster.TuringUsbDisplay.send_jpeg = send_jpeg
  extra = sys.argv[1:]
  sys.argv = [sys.argv[0], '--input', 'live', '--live-no-can', '--output', 'usb', '--usb-codec', 'jpeg',
              '--width', '1920', '--height', '480', '--fps', '10', '--cluster-hud-mode', str(mode), *extra]
  if '--help' in sys.argv:
    cluster.main()
  else:
    from display_owner import panel_owner
    with panel_owner(hud=True):
      cluster.main()


if __name__ == '__main__':
  main()
