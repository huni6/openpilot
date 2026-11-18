"""NewUi 0-3 on the comma 4 onroad screen: setting, layout, alerts and the eGPU / Jetson badges.

0 = Carrot UI, 1 = Wayon/sunnypilot style, 2 = clock face, 3 = robot face (numbered as on WIP-FR)."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock  # noqa: TID251

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def ui(monkeypatch):
  pytest.importorskip('capnp')
  from openpilot.selfdrive.ui.tests.test_jetlink_badge import load_ui_modules
  existing = set(sys.modules)
  yield load_ui_modules(monkeypatch)
  for name in set(sys.modules) - existing:
    if name.startswith(('openpilot.selfdrive.ui.', 'openpilot.system.ui.widgets')):
      sys.modules.pop(name, None)


def load(monkeypatch, name):
  fullname = 'openpilot.' + name
  spec = importlib.util.spec_from_file_location(fullname, ROOT / (name.replace('.', '/') + '.py'))
  module = importlib.util.module_from_spec(spec)
  monkeypatch.setitem(sys.modules, fullname, module)
  spec.loader.exec_module(module)
  return module


@pytest.mark.parametrize(('stored', 'mode'), [(0, 0), (1, 1), (2, 2), (3, 3), (4, 0), (-1, 0), (None, 0), (True, 0),
                                              (False, 0), ('2', 0), (2.0, 0)])
def test_new_ui_mode_takes_only_the_integers_0_to_3(ui, stored, mode):
  assert ui.state.new_ui_mode(stored) == mode


@pytest.mark.parametrize(('mode', 'flags'), [(0, (False, False, False)), (1, (True, False, False)),
                                             (2, (True, True, False)), (3, (True, False, True))])
def test_each_mode_sets_its_layout_flags(ui, monkeypatch, mode, flags):
  state = ui.state.ui_state
  state.params = NS(get_int=lambda key: mode)
  state._new_ui_update_time = float('-inf')
  state._update_new_ui()
  assert (state.new_ui, state.clock_ui, state.robot_ui) == flags and state.new_ui_mode == mode


def test_setting_is_read_twice_a_second_and_never_raises(ui, monkeypatch):
  state = ui.state.ui_state
  reads = []
  value = {'NewUi': 3}
  state.params = NS(get_int=lambda key: reads.append(key) or value[key])
  state._new_ui_update_time = float('-inf')
  for now in (100., 100.3, 100.6):
    monkeypatch.setattr(ui.state.time, 'monotonic', lambda now=now: now)
    state._update_new_ui()
  assert reads == ['NewUi', 'NewUi'] and state.robot_ui and state.new_ui_mode == 3
  value['NewUi'] = 2
  monkeypatch.setattr(ui.state.time, 'monotonic', lambda: 101.2)
  state._update_new_ui()
  assert state.clock_ui and state.new_ui and not state.robot_ui     # another face without a reboot
  value['NewUi'] = 0
  monkeypatch.setattr(ui.state.time, 'monotonic', lambda: 101.8)
  state._update_new_ui()
  assert not (state.robot_ui or state.clock_ui or state.new_ui)    # back to the Carrot UI
  state.robot_ui = state.clock_ui = state.new_ui = True
  state.params = NS(get_int=Mock(side_effect=OSError()))
  monkeypatch.setattr(ui.state.time, 'monotonic', lambda: 102.4)
  state._update_new_ui()
  assert not (state.robot_ui or state.clock_ui or state.new_ui) and state.new_ui_mode == 0


@pytest.mark.parametrize('mode', ['robot', 'clock', 'wayon', 'carrot'])
def test_only_the_robot_face_hides_the_alerts_it_reacts_to(ui, monkeypatch, mode):
  alerts = load(monkeypatch, 'selfdrive.ui.mici.onroad.alert_renderer')
  status = alerts.AlertStatus
  renderer = object.__new__(alerts.AlertRenderer)
  state = ui.state.ui_state
  state.new_ui, state.clock_ui, state.robot_ui = mode != 'carrot', mode == 'clock', mode == 'robot'
  for level in (status.normal, status.userPrompt, status.critical):
    shown = mode != 'robot' or level == status.critical
    alert = alerts.Alert(text1='Take Control', status=level)
    renderer.get_alert = lambda sm, alert=alert: alert
    renderer._prev_alert = alerts.Alert(text1='old')
    to_render, gone = renderer.will_render()
    assert (to_render is alert) == shown and gone == (not shown)
    if not shown:
      assert renderer._prev_alert is None                    # nothing lingers over the face either


def test_compact_hud_draws_only_the_egpu_badge_over_the_face(ui, monkeypatch):
  import pyray as rl
  from openpilot.selfdrive.ui.tests.test_egpu_badge import hud
  renderer, calls = hud(ui, 'compact', monkeypatch)
  ui.state.ui_state.egpu_badge = ('eGPU', 'active')
  renderer.draw_egpu_status(rl.Rectangle(0, 0, 536, 240))  # full width: no status rail under the face
  ((status, bounds, font_size),) = calls
  assert status == ('eGPU', 'active') and font_size == 22
  assert bounds.x + bounds.width == 536 - 24 and bounds.y == 12
  renderer._draw_set_speed.assert_not_called()
  renderer._draw_steering_wheel.assert_not_called()


@pytest.fixture
def road_view(ui, monkeypatch):
  import pyray as rl
  from openpilot.system.ui.widgets import Widget
  calls = []
  alert = [None]

  class CameraView(Widget):
    def __init__(self, *args, **kwargs):
      pass

    def _render(self, rect):
      calls.append(('camera', rect.width))

  camera = ModuleType('openpilot.selfdrive.ui.mici.onroad.cameraview')
  camera.CameraView = CameraView
  monkeypatch.setitem(sys.modules, camera.__name__, camera)
  # no camera here, and msgq's native part is not built on a Mac
  msgq, visionipc = ModuleType('msgq'), ModuleType('msgq.visionipc')
  visionipc.VisionStreamType = NS(VISION_STREAM_ROAD=0, VISION_STREAM_WIDE_ROAD=2)
  msgq.visionipc = visionipc
  monkeypatch.setitem(sys.modules, 'msgq', msgq)
  monkeypatch.setitem(sys.modules, 'msgq.visionipc', visionipc)
  # the overlays are replaced by recorders below; their modules pull in calibration and messaging
  for mod, cls in (('model_renderer', 'ModelRenderer'), ('driver_state', 'DriverStateRenderer'),
                   ('confidence_ball', 'ConfidenceBall'), ('traffic_light', 'TrafficLight')):
    stub = ModuleType(f'openpilot.selfdrive.ui.mici.onroad.{mod}')
    setattr(stub, cls, Mock)
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
  for name in ('begin_scissor_mode', 'end_scissor_mode', 'draw_rectangle_rounded_lines_ex',
               'draw_rectangle', 'draw_rectangle_rec', 'draw_circle'):
    monkeypatch.setattr(rl, name, Mock())
  # the bottom fade: drawn as is in the Carrot UI, stretched over the wider NewUi 1 view
  monkeypatch.setattr(rl, 'draw_texture_ex', Mock(side_effect=lambda *a: calls.append(('fade', 476))))
  monkeypatch.setattr(rl, 'draw_texture_pro', Mock(side_effect=lambda tex, src, dst, *a: calls.append(('fade', dst.width))))
  module = load(monkeypatch, 'selfdrive.ui.mici.onroad.augmented_road_view')
  monkeypatch.setattr(module, 'messaging', NS(new_message=lambda *a, **k: NS(uiDebug=NS())))
  monkeypatch.setattr(module.gui_app, 'is_recording', lambda: False, raising=False)

  view = object.__new__(module.AugmentedRoadView)
  view._rect = rl.Rectangle(0, 0, 536, 240)
  view._switch_stream_if_needed = Mock()
  view._update_calibration = Mock()
  view._road_view_mode = lambda: 0
  view._pm = Mock()
  rec = lambda tag: Mock(side_effect=lambda rect, *a: calls.append((tag, rect.width)))  # noqa: E731
  view._model_renderer = NS(render=rec('model'))
  view._hud_renderer = NS(render=rec('hud'), draw_egpu_status=rec('egpu'), draw_jetlink_status=rec('jetson'),
                          drawing_top_icons=lambda: False, set_can_draw_top_icons=Mock(), set_wheel_critical_icon=Mock())
  view._alert_renderer = NS(will_render=lambda: (alert[0], True), render=rec('alert'))
  view._driver_state_renderer = NS(set_should_draw=Mock(side_effect=lambda on: calls.append(('dm', on))), set_position=Mock(),
                                   render=Mock())
  view._traffic_light = NS(render=Mock(side_effect=lambda r: calls.append(('traffic', 0))), is_visible=lambda: False)
  view._confidence_ball = NS(render=Mock(side_effect=lambda r: calls.append(('ball', 0))))
  view._bookmark_icon = NS(render=Mock())
  view._offroad_label = NS(render=Mock())
  view._fade_texture = NS(width=476, height=240)
  view._robot_face = NS(render=rec('robot'))
  view._clock_face = NS(render=rec('clock'))
  state = ui.state.ui_state
  state.started, state.status, state.always_on_dm = True, ui.state.UIStatus.ENGAGED, False

  def set_mode(mode):
    state.new_ui, state.clock_ui, state.robot_ui = mode >= 1, mode == 2, mode == 3
  return NS(view=view, calls=calls, state=state, side=module.SIDE_PANEL_WIDTH, set_mode=set_mode, alert=alert,
            hud=view._hud_renderer)


@pytest.mark.parametrize(('mode', 'face'), [(3, 'robot'), (2, 'clock')])
def test_a_face_replaces_camera_model_hud_and_dm_on_the_full_width(road_view, mode, face):
  road_view.set_mode(mode)
  road_view.view._render(None)
  calls = dict(road_view.calls)
  assert calls[face] == 536 and calls['egpu'] == 536 and calls['alert'] == 536 and calls['jetson'] == 536
  assert not {'camera', 'model', 'hud', 'ball', 'traffic', 'fade', 'robot' if face == 'clock' else 'clock'} & calls.keys()
  assert calls['dm'] is False


def test_wayon_keeps_camera_model_and_hud_on_the_full_width_without_the_right_rail(road_view):
  road_view.set_mode(1)
  road_view.view._render(None)
  calls = dict(road_view.calls)
  assert calls['camera'] == calls['model'] == calls['hud'] == calls['fade'] == calls['alert'] == calls['jetson'] == 536
  assert not {'robot', 'clock', 'egpu', 'ball', 'traffic'} & calls.keys()   # the HUD draws eGPU itself in NewUi 1
  assert calls['dm'] is True
  road_view.calls.clear()
  road_view.alert[0] = NS(visual_alert=None)                 # an alert takes the top: the DM icon steps aside
  road_view.view._render(None)
  assert dict(road_view.calls)['dm'] is False


def test_carrot_ui_is_unchanged_and_offroad_shows_no_face(road_view):
  road_view.set_mode(0)
  road_view.view._render(None)
  calls = dict(road_view.calls)
  narrow = 536 - road_view.side
  assert calls['camera'] == calls['model'] == calls['hud'] == narrow and calls['fade'] == 476
  assert 'robot' not in calls and 'clock' not in calls and 'egpu' not in calls and 'ball' in calls and calls['dm'] is True
  road_view.calls.clear()
  road_view.alert[0] = NS(visual_alert=None)                 # the Carrot UI keeps its DM icon under alerts
  road_view.view._render(None)
  assert dict(road_view.calls)['dm'] is True
  road_view.alert[0] = None
  for mode in (2, 3):
    road_view.calls.clear()
    road_view.set_mode(mode)
    road_view.state.started = False                          # offroad: no face, the NewUi full-width camera page
    road_view.view._render(None)
    calls = dict(road_view.calls)
    assert 'robot' not in calls and 'clock' not in calls and calls['camera'] == 536 and 'ball' not in calls
    road_view.state.started = True


@pytest.mark.parametrize('egpu', [None, ('eGPU', 'active')])
def test_wayon_puts_the_jetson_badge_top_right_under_egpu_and_clears_it_for_alerts(ui, monkeypatch, egpu):
  import pyray as rl
  from openpilot.selfdrive.ui.tests.test_egpu_badge import hud
  renderer, calls = hud(ui, 'compact', monkeypatch)
  state = ui.state.ui_state
  state.new_ui, state.clock_ui, state.robot_ui = True, False, False
  state.egpu_badge = egpu
  state.jetlink_badge, state.jetlink_detail = ('jetSON READY', 'ready'), ''
  renderer.draw_jetlink_status(rl.Rectangle(0, 0, 536, 240))
  ((status, bounds, _),) = calls
  assert status == ('jetSON READY', 'ready') and bounds.x + bounds.width == 536 - 26
  assert bounds.y == (52 if egpu else 24)                    # under the eGPU badge (y 12-46) when it is there
  calls.clear()
  renderer.draw_jetlink_status(rl.Rectangle(0, 0, 536, 240), NS(status=0, size=0))
  assert calls == []
  for face in ('clock_ui', 'robot_ui'):                      # 2 and 3 keep the bottom-right place
    calls.clear()
    setattr(state, face, True)
    renderer.draw_jetlink_status(rl.Rectangle(0, 0, 536, 240))
    assert calls[0][1].y >= 240 - 110
    setattr(state, face, False)
