"""NewUi 3 robot face on the comma 4 onroad screen: setting, layout, alerts and the eGPU badge over it."""
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


@pytest.mark.parametrize(('stored', 'mode'), [(3, 3), (0, 0), (1, 0), (2, 0), (4, 0), (-1, 0), (None, 0), (True, 0)])
def test_only_the_robot_value_switches_the_display(ui, stored, mode):
  # numbered as on WIP-FR; its 1 (Wayon) and 2 (clock) do not exist here and keep the Carrot UI
  assert ui.state.new_ui_mode(stored) == mode


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
  value['NewUi'] = 0
  monkeypatch.setattr(ui.state.time, 'monotonic', lambda: 101.2)
  state._update_new_ui()
  assert not state.robot_ui                                  # back to the Carrot UI without a reboot
  state.robot_ui = True
  state.params = NS(get_int=Mock(side_effect=OSError()))
  monkeypatch.setattr(ui.state.time, 'monotonic', lambda: 102.)
  state._update_new_ui()
  assert not state.robot_ui and state.new_ui_mode == 0


def test_robot_face_hides_alerts_it_reacts_to_but_not_critical_ones(ui, monkeypatch):
  alerts = load(monkeypatch, 'selfdrive.ui.mici.onroad.alert_renderer')
  status = alerts.AlertStatus
  renderer = object.__new__(alerts.AlertRenderer)
  state = ui.state.ui_state
  for robot, level, shown in ((True, status.normal, False), (True, status.userPrompt, False), (True, status.critical, True),
                              (False, status.normal, True), (False, status.userPrompt, True)):
    alert = alerts.Alert(text1='Take Control', status=level)
    renderer.get_alert = lambda sm, alert=alert: alert
    renderer._prev_alert = alerts.Alert(text1='old')
    state.robot_ui = robot
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
  for name in ('begin_scissor_mode', 'end_scissor_mode', 'draw_texture_ex', 'draw_rectangle_rounded_lines_ex',
               'draw_rectangle', 'draw_rectangle_rec', 'draw_circle'):
    monkeypatch.setattr(rl, name, Mock())
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
  view._alert_renderer = NS(will_render=lambda: (None, True), render=rec('alert'))
  view._driver_state_renderer = NS(set_should_draw=Mock(side_effect=lambda on: calls.append(('dm', on))), set_position=Mock(),
                                   render=Mock())
  view._traffic_light = NS(render=Mock(side_effect=lambda r: calls.append(('traffic', 0))), is_visible=lambda: False)
  view._confidence_ball = NS(render=Mock(side_effect=lambda r: calls.append(('ball', 0))))
  view._bookmark_icon = NS(render=Mock())
  view._offroad_label = NS(render=Mock())
  view._fade_texture = None
  view._robot_face = NS(render=rec('robot'))
  state = ui.state.ui_state
  state.started, state.status, state.always_on_dm = True, ui.state.UIStatus.ENGAGED, False
  return NS(view=view, calls=calls, state=state, side=module.SIDE_PANEL_WIDTH)


def test_robot_face_replaces_camera_model_hud_and_dm_on_the_full_width(road_view):
  road_view.state.robot_ui = True
  road_view.view._render(None)
  calls = dict(road_view.calls)
  assert calls['robot'] == 536 and calls['egpu'] == 536 and calls['alert'] == 536 and calls['jetson'] == 536
  assert not {'camera', 'model', 'hud', 'ball', 'traffic'} & calls.keys()
  assert calls['dm'] is False


def test_carrot_ui_is_unchanged_when_the_robot_is_off_or_offroad(road_view):
  road_view.state.robot_ui = False
  road_view.view._render(None)
  calls = dict(road_view.calls)
  narrow = 536 - road_view.side
  assert calls['camera'] == calls['model'] == calls['hud'] == narrow
  assert 'robot' not in calls and 'egpu' not in calls and 'ball' in calls and calls['dm'] is True
  road_view.calls.clear()
  road_view.state.robot_ui, road_view.state.started = True, False   # offroad: the setting changes nothing
  road_view.view._render(None)
  calls = dict(road_view.calls)
  assert 'robot' not in calls and calls['camera'] == narrow
