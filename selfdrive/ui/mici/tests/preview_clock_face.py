"""Offline native preview of the NewUi=2 clock face. Synthetic inputs, no device or publishers.

Ported from WIP-FR 5325a288. Also draws the eGPU badge where the comma 4 shows it over the clock. Run with SCALE=1 on
a Mac whose display is asleep (the auto scale asks the monitor for its size).
"""
import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import pyray as rl

CASES = ('day', 'day-engaged', 'night', 'night-engaged', 'imperial', 'no-car-state', 'fade', 'egpu')
# 2026-11-17 22:10:42 KST, a Tuesday.
PREVIEW_TS = datetime(2026, 11, 17, 13, 10, 42, 300000, tzinfo=UTC).timestamp()


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--case', choices=CASES, action='append', help='Render only these cases (repeatable).')
  parser.add_argument('--timestamp', type=float, default=PREVIEW_TS, help='Unix time to show (KST is applied).')
  args = parser.parse_args()
  args.output.mkdir(parents=True, exist_ok=True)

  class SM(dict):
    alive = {'carState': True, 'carControl': True}
    recv_frame = {'carState': 101, 'carControl': 101}
  sm = SM(carState=NS(vEgo=62 / 3.6, vEgoCluster=62 / 3.6), carControl=NS(latActive=False))
  ui = NS(started=True, started_frame=100, is_metric=True, sm=sm)
  state_module = ModuleType('openpilot.selfdrive.ui.ui_state')
  state_module.ui_state = ui
  sys.modules[state_module.__name__] = state_module
  from openpilot.system.ui.lib.application import gui_app
  from openpilot.selfdrive.ui.mici.onroad.clock_face import ClockFace
  from openpilot.selfdrive.ui.onroad.jetlink_badge import draw_jetlink_badge

  gui_app.init_window('C4 clock face preview')  # this branch loads every font weight
  try:
    rect = rl.Rectangle(0, 0, 536, 240)
    selected = args.case or CASES
    for case in selected:
      face = ClockFace()
      ui.is_metric = case != 'imperial'
      sm.alive['carState'] = case != 'no-car-state'
      sm['carControl'].latActive = case.endswith('engaged') or case == 'egpu'
      ui.started_frame += 1
      sm.recv_frame = dict.fromkeys(sm.recv_frame, ui.started_frame + 1)
      night = case.startswith('night')
      rl.begin_drawing()
      rl.clear_background(rl.BLACK)
      if case == 'fade':
        # Daylight, then past sunset: capture about 0.35 s into the 0.7 s fade.
        face.render(rect, args.timestamp - 6 * 3600, None)
        for _ in range(7):
          face.render(rect, args.timestamp, None)
      else:
        face.render(rect, args.timestamp, night)
      if case == 'egpu':
        # HudRenderer.draw_egpu_status over the clock
        draw_jetlink_badge(('eGPU', 'active'), rl.Rectangle(rect.x + rect.width - 24 - 160, rect.y + 12, 160, 34), 22)
      rl.rl_draw_render_batch_active()
      image = rl.load_image_from_screen()
      try:
        if (image.width, image.height) != (536, 240):
          # High-DPI desktops read back a scaled framebuffer.
          rl.image_resize(image, 536, 240)
        assert rl.export_image(image, str(args.output / f'{case}.png'))
      finally:
        rl.unload_image(image)
      rl.end_drawing()
    print(f'PASS: {len(selected)} native 536x240 clock frames; synthetic inputs, not a vehicle capture')
  finally:
    gui_app.close()


if __name__ == '__main__':
  main()
