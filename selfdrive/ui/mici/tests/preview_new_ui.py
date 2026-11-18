"""Offline native preview of the NewUi=1 (Wayon) HUD on the comma 4. Synthetic inputs, no device or publishers.

Ported from WIP-FR 5325a288 without its deceleration badge, stop timers, vision card and DM cases (not on this
branch). Draws a stand-in road (no camera here), the lead bar, the eGPU and Jetson badges where HudRenderer puts them
in NewUi 1, and the real WayonHud. Run with SCALE=1 on a Mac whose display is asleep.
"""
import argparse
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace as NS

import pyray as rl

CASES = ('set-speed', 'steady', 'disengaged', 'lateral-only', 'bsd', 'bsm-left', 'bsm-right', 'critical',
         'three-digits', 'lead-radar', 'lead-vision', 'egpu', 'egpu-jetson')


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--case', choices=CASES, action='append', help='Render only these cases (repeatable).')
  args = parser.parse_args()
  args.output.mkdir(parents=True, exist_ok=True)

  class SM(dict):
    valid = dict.fromkeys(('carState', 'selfdriveState', 'carControl', 'controlsState', 'carOutput', 'liveParameters'), True)
    alive = valid.copy()
    recv_frame = dict.fromkeys(valid, 101)
    seen = valid.copy()
    recv_time = dict.fromkeys(valid, time.monotonic())
  car = NS(leftBlindspot=False, rightBlindspot=False, steeringAngleDeg=4, vEgo=20.)
  sm = SM(carState=car, carControl=NS(latActive=False, longActive=True), selfdriveState=NS(enabled=True),
          controlsState=NS(lateralControlState=NS(which=lambda: 'torqueState')), carOutput=NS(actuatorsOutput=NS(torque=-.45)),
          liveParameters=NS(roll=0.))
  ui = NS(started=True, started_frame=100, status='engaged', is_metric=True, sm=sm, new_ui=True, CP=None)
  # Inject only UI inputs; keep real widgets, fonts, textures, filters and GPU draw calls.
  state_module = ModuleType('openpilot.selfdrive.ui.ui_state')
  state_module.ui_state = ui
  state_module.UIStatus = NS(DISENGAGED='disengaged', ENGAGED='engaged', OVERRIDE='override')
  sys.modules[state_module.__name__] = state_module
  from openpilot.system.ui.lib.application import FontWeight, gui_app
  from openpilot.selfdrive.ui.mici.onroad.wayon_hud import WayonHud
  from openpilot.selfdrive.ui.mici.onroad.lead_bar import LeadBar, LeadBarTarget
  from openpilot.selfdrive.ui.onroad.jetlink_badge import draw_jetlink_badge

  gui_app.init_window('C4 new ui native preview')  # this branch loads every font weight
  real_clock = rl.get_time
  preview_time = [10.]
  rl.get_time = lambda: preview_time[0]
  try:
    renderer = WayonHud()
    hud = NS(_can_draw_top_icons=True, _show_wheel_critical=False,
             is_cruise_set=True, _engaged=True, set_speed=100, _set_speed_changed_time=rl.get_time(),
             _font_display=gui_app.font(FontWeight.DISPLAY), _font_semi_bold=gui_app.font(FontWeight.SEMI_BOLD),
             _turn_intent=NS(render=lambda _: None))
    rect = rl.Rectangle(0, 0, 536, 240)
    selected_cases = args.case or CASES
    for case in selected_cases:
      preview_time[0] = 10.
      renderer.reset()
      bar = LeadBar()
      ui.status = 'disengaged' if case in ('disengaged', 'lateral-only') else 'engaged'
      hud._engaged = case not in ('disengaged', 'lateral-only')
      sm['carControl'].latActive = case != 'disengaged'
      sm['selfdriveState'].enabled = hud._engaged
      hud._show_wheel_critical = case == 'critical'
      hud._can_draw_top_icons = case != 'critical'
      hud.set_speed = 254 if case == 'three-digits' else 100
      hud._set_speed_changed_time = rl.get_time() if case in ('set-speed', 'three-digits', 'bsm-left') else -10
      car.leftBlindspot = case in ('bsd', 'bsm-left')
      car.rightBlindspot = case in ('bsd', 'bsm-right')
      for frame in range(40):
        rl.begin_drawing()
        # stand-in road: dark asphalt and two lane lines toward a vanishing point
        rl.clear_background(rl.Color(38, 40, 43, 255))
        rl.draw_rectangle(0, 0, 536, 96, rl.Color(70, 86, 104, 255))
        for x0, x1 in ((120, 250), (416, 286)):
          rl.draw_line_ex(rl.Vector2(x0, 240), rl.Vector2(x1, 96), 4, rl.Color(255, 255, 255, 150))
        if case.startswith('lead-'):
          rl.draw_rectangle(240, 112, 56, 34, rl.Color(18, 18, 20, 255))
          bar.draw(LeadBarTarget(268, 146, 50, case == 'lead-radar', 3), frame * 0.05)
        renderer.draw(rect, hud)
        if case.startswith('egpu') and hud._can_draw_top_icons:
          # HudRenderer.draw_egpu_status and NewUi 1 draw_jetlink_status
          draw_jetlink_badge(('eGPU', 'active'), rl.Rectangle(rect.x + rect.width - 24 - 160, rect.y + 12, 160, 34), 22)
          if case == 'egpu-jetson':
            draw_jetlink_badge(('jetSON READY', 'ready'), rl.Rectangle(rect.x + rect.width - 106, rect.y + 52, 80, 42), 13,
                               stacked=True)
        if frame == 39:
          rl.rl_draw_render_batch_active()
          image = rl.load_image_from_screen()
          try:
            if (image.width, image.height) != (536, 240):
              rl.image_resize(image, 536, 240)
            assert rl.export_image(image, str(args.output / f'{case}.png'))
          finally:
            rl.unload_image(image)
        rl.end_drawing()
    print(f'PASS: {len(selected_cases)} native 536x240 HUD frames; isolated synthetic inputs, not a vehicle capture')
  finally:
    rl.get_time = real_clock
    gui_app.close()


if __name__ == '__main__':
  main()
