"""Render local vs transported state with the real raylib renderer, not HTML."""
import argparse
import base64
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import tempfile
from unittest.mock import patch  # noqa: TID251 -- freeze clocks in this standalone visual verifier

from PIL import Image, ImageChops, ImageStat
from hud_protocol import HEADER, PARAM_DEFAULTS, encode_snapshot
from hud import RemoteState
from cluster_live import standby_state
from cluster_models import PhoneMediaInfo
from cluster_renderer import ClusterUiRenderer


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('output', type=Path)
  args = parser.parse_args()
  args.output.mkdir(parents=True, exist_ok=True)
  # A deterministic, clearly synthetic album cover exercises actual PNG decode.
  cover = Image.new('RGB', (180, 180), '#267b64')
  buffer = BytesIO()
  cover.save(buffer, format='PNG')
  art = base64.b64encode(buffer.getvalue()).decode()
  state = replace(standby_state(), speed_kph=87., accel_mps2=.7, gear_text='D', cruise_kph=100,
                  cruise_gap=2, cruise_display_state='engaged', lfa_active=True, speed_limit_kph=80,
                  left_blindspot=True, phone_media=PhoneMediaInfo(title='이등병의 편지', artist='김광석',
                  is_playing=True, duration_ms=240000, position_ms=105000, received_at_ms=100000000,
                  art_base64=art, art_mime='image/png'))
  with tempfile.TemporaryDirectory() as directory, patch('time.time', return_value=100000.), patch('time.monotonic', return_value=10.):
    root = Path(directory)
    body, artwork = encode_snapshot(state, PARAM_DEFAULTS, 100000.)
    (root / 'packet').write_bytes(HEADER.pack(10.) + HEADER.pack(0.) + body)
    (root / 'art').write_bytes(artwork)
    transported = RemoteState(root / 'packet', root / 'art').current()
    for theme in ('dark', 'light'):
      renderer = ClusterUiRenderer(width=1920, height=480, theme_mode=theme)
      renderer.open(hidden=True)
      try:
        renderer._ambient_bsm_left_opacity = 1.
        renderer.render_to_file(state, args.output / (theme + '-local.png'))
        renderer.render_to_file(transported, args.output / (theme + '-jetson.png'))
        assert not renderer._ambient_step_errors_logged, renderer._ambient_step_errors_logged
      finally:
        renderer.close()
      local = Image.open(args.output / (theme + '-local.png')).convert('RGB')
      remote = Image.open(args.output / (theme + '-jetson.png')).convert('RGB')
      assert ImageChops.difference(local, remote).getbbox() is None, 'Transport changed rendered pixels'
      assert max(ImageStat.Stat(remote).stddev) > 10, 'Blank display'
      print(theme, '1920x480 pixel-identical; Korean text, art, speed, cruise, BSM rendered')


if __name__ == '__main__':
  main()
