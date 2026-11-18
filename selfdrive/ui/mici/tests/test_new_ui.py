"""NewUi 1 (Wayon) HUD on the comma 4. Ported from WIP-FR 5325a288 without its deceleration badge and stop timers,
which this branch does not have."""
import ast
import importlib.util
import sys
import time
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ONROAD = Path(__file__).parents[1] / 'onroad'


@pytest.fixture
def scene(monkeypatch):
  calls = []
  now = [10.]
  class Filter:
    def __init__(self, x, *_args):
      self.x = x
    def update(self, x):
      self.x = x
      return x
  class Torque:
    def __init__(self):
      self._torque_filter = Filter(0)
      self._torque_line_alpha_filter = Filter(0)
    def render(self, rect):
      calls.append(('torque', rect))
  rl = NS(Rectangle=namedtuple('Rectangle', 'x y width height'), Vector2=namedtuple('Vector2', 'x y'),
          Color=namedtuple('Color', 'r g b a'), WHITE='white', BLANK='blank', get_time=lambda: now[0])
  for name in ('draw_texture_pro', 'draw_texture_ex', 'draw_text_ex', 'draw_circle_gradient', 'draw_rectangle_rounded'):
    setattr(rl, name, lambda *args, name=name: calls.append((name, *args)))
  class SM(dict):
    valid = dict.fromkeys(('carState', 'selfdriveState', 'carControl', 'carrotMan', 'longitudinalPlan'), True)
    alive = valid.copy()
    recv_frame = dict.fromkeys(valid, 101)
    seen = valid.copy()
    recv_time = dict.fromkeys(valid, time.monotonic())
  cs = NS(leftBlindspot=False, rightBlindspot=False, steeringAngleDeg=5, vCruise=100, gasPressed=False, brakePressed=False)
  sm = SM(carState=cs, carControl=NS(latActive=False, longActive=True), selfdriveState=NS(enabled=True),
          carrotMan=NS(desiredSpeed=250, desiredSource='none'), longitudinalPlan=NS(cruiseTarget=100))
  ui = NS(started=True, started_frame=100, status='engaged', is_metric=True, sm=sm, new_ui=True)
  gui = NS(target_fps=20, texture=lambda name, w, h, **kw: NS(name=name, width=w, height=h, flip_x=kw.get('flip_x', False)))
  for name, value in {
    'pyray': rl,
    'openpilot.common.filter_simple': NS(FirstOrderFilter=Filter),
    'openpilot.selfdrive.ui.ui_state': NS(ui_state=ui, UIStatus=NS(DISENGAGED='disengaged')),
    'openpilot.system.ui.lib.application': NS(gui_app=gui),
    'openpilot.system.ui.lib.multilang': NS(tr=lambda text: text),
    'openpilot.system.ui.lib.text_measure': NS(measure_text_cached=lambda _, text, size: NS(x=len(text) * size * .6, y=size)),
    'openpilot.selfdrive.ui.mici.onroad.torque_bar': NS(TorqueBar=Torque),
  }.items():
    monkeypatch.setitem(sys.modules, name, value)
  bsm_name = 'openpilot.selfdrive.ui.mici.onroad.blind_spot_indicators'
  bsm_spec = importlib.util.spec_from_file_location(bsm_name, ONROAD / 'blind_spot_indicators.py')
  bsm_module = importlib.util.module_from_spec(bsm_spec)
  monkeypatch.setitem(sys.modules, bsm_name, bsm_module)
  bsm_spec.loader.exec_module(bsm_module)
  spec = importlib.util.spec_from_file_location('_new_ui_test', ONROAD / 'wayon_hud.py')
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  hud = NS(_can_draw_top_icons=True, _show_wheel_critical=False,
           is_cruise_set=True, _engaged=True, set_speed=100, _set_speed_changed_time=10,
           _font_display='display', _font_semi_bold='semi', _turn_intent=NS(render=lambda rect: calls.append(('turn', rect))))
  return NS(renderer=module.WayonHud(), module=module, ui=ui, sm=sm, cs=cs, hud=hud, calls=calls, now=now,
            rect=rl.Rectangle(0, 0, 536, 240))


def texts(scene):
  return [call[2] for call in scene.calls if call[0] == 'draw_text_ex']


def test_max_is_temporary_and_uses_real_selected_speed(scene):
  s = scene
  s.renderer.draw(s.rect, s.hud)
  assert texts(s) == ['100', 'MAX']
  assert s.renderer.drawing_top_icons()
  s.now[0] += 2.6
  s.calls.clear()
  s.renderer.draw(s.rect, s.hud)
  assert not texts(s) and not s.renderer.drawing_top_icons()
  assert any(call[0] == 'torque' for call in s.calls)
  s.hud.set_speed = 120
  s.hud._set_speed_changed_time = s.now[0]
  s.ui.is_metric = False
  s.renderer.draw(s.rect, s.hud)
  assert texts(s) == ['75', 'MAX']


@pytest.mark.parametrize('condition', ['warning', 'stale', 'old_session', 'offroad'])
def test_warning_and_bad_inputs_clear_max_immediately(scene, condition):
  s = scene
  s.renderer.draw(s.rect, s.hud)
  s.calls.clear()
  if condition == 'warning':
    s.hud._can_draw_top_icons = False
  elif condition == 'stale':
    s.sm.alive = dict(s.sm.alive, carState=False)
  elif condition == 'old_session':
    s.ui.started_frame = 102
  else:
    s.ui.started = False
  s.renderer.draw(s.rect, s.hud)
  assert not texts(s) and not s.renderer.drawing_top_icons()
  assert any(call[0] == 'torque' for call in s.calls) == (condition == 'warning')


def test_bsd_hides_ordinary_wheel_but_not_critical_wheel(scene):
  s = scene
  s.cs.leftBlindspot = True
  s.renderer.draw(s.rect, s.hud)
  assert next(call for call in s.calls if call[0] == 'draw_texture_pro')[-1].a == 0
  s.calls.clear()
  s.hud._show_wheel_critical = True
  s.renderer.draw(s.rect, s.hud)
  wheel = next(call for call in s.calls if call[0] == 'draw_texture_pro')
  assert wheel[1].name.endswith('wheel_critical.png') and wheel[-1].a == 255
  assert any(call[0] == 'draw_texture_ex' for call in s.calls)


def test_disengaged_and_reset_remove_old_presentation(scene):
  s = scene
  s.hud._engaged = False
  s.ui.status = 'disengaged'
  s.renderer.draw(s.rect, s.hud)
  assert not texts(s)
  s.renderer._speed_alpha.x = 1
  s.renderer._torque_bar._torque_filter.x = .8
  s.renderer.reset()
  assert not s.renderer.drawing_top_icons()
  assert s.renderer._torque_bar._torque_filter.x == 0


@pytest.mark.parametrize('blindspot', [False, True])
def test_lateral_only_keeps_torque_and_yields_wheel_to_wayon_bsm(scene, blindspot):
  s = scene
  s.ui.status = 'disengaged'
  s.hud._engaged = False
  s.sm['carControl'].latActive = True
  s.cs.leftBlindspot = blindspot
  s.renderer.draw(s.rect, s.hud)
  color = next(call for call in s.calls if call[0] == 'draw_texture_pro')[-1]
  assert color.r == 0 and color.g == 255
  assert (color.a > 0) == (not blindspot)
  assert s.renderer._torque_bar.lateral_active
  assert bool(bsm_icons(s)) == blindspot
  assert not texts(s)
  s.sm.alive = dict(s.sm.alive, carControl=False)
  s.calls.clear()
  s.renderer.draw(s.rect, s.hud)
  assert not s.renderer._torque_bar.lateral_active
  assert next(call for call in s.calls if call[0] == 'draw_texture_pro')[-1].a == 0


def test_lane_change_alert_keeps_turn_intent_below_alert(scene):
  s = scene
  s.hud._can_draw_top_icons = False
  s.renderer.draw(s.rect, s.hud)
  assert any(call[0] == 'turn' for call in s.calls)
  assert not texts(s)


def test_real_hud_dispatch_reuses_one_wayon_instance_and_restores_legacy(scene, monkeypatch):
  s = scene
  tree = ast.parse((ONROAD / 'hud_renderer.py').read_text())
  klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'HudRenderer')
  method = next(node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name == '_render')
  namespace = {'rl': s.module.rl, 'ui_state': s.ui}
  exec(compile(ast.Module(body=[method], type_ignores=[]), str(ONROAD / 'hud_renderer.py'), 'exec'), namespace)
  allocations, draws = [], []
  def create():
    allocations.append(True)
    return s.renderer
  monkeypatch.setitem(sys.modules, 'openpilot.selfdrive.ui.mici.onroad.wayon_hud', NS(WayonHud=create))
  h = s.hud
  h._new_ui_active, h._wayon_hud = False, None
  h._draw_set_speed = lambda rect: draws.append('legacy')
  h._draw_steering_wheel = lambda rect: draws.append('wheel')
  h.draw_egpu_status = lambda rect: draws.append('egpu')
  h._draw_traffic_light_info = lambda *args: draws.append('traffic')
  for _ in range(20):
    s.ui.new_ui = True
    namespace['_render'](h, s.rect)
    assert draws[-2:] == ['traffic', 'egpu']
    s.ui.new_ui = False
    namespace['_render'](h, s.rect)
    assert draws[-3:] == ['legacy', 'wheel', 'egpu']
  assert len(allocations) == 1
  h._can_draw_top_icons = False        # an alert owns the top: no traffic countdown or eGPU badge under it
  draws.clear()
  s.ui.new_ui = True
  namespace['_render'](h, s.rect)
  assert draws == []


def test_new_ui_is_display_only_and_existing_overlay_owners_remain():
  text = (ONROAD / 'augmented_road_view.py').read_text()
  for fragment in ('self._model_renderer.render', 'self._alert_renderer.render', 'self._traffic_light.render',
                   'self._hud_renderer.draw_jetlink_status', 'self._hud_renderer.draw_egpu_status'):
    assert fragment in text
  for name in ('wayon_hud.py', 'blind_spot_indicators.py', 'lead_bar.py', 'clock_face.py'):
    source = (ONROAD / name).read_text()
    assert all(forbidden not in source for forbidden in ('PubMaster', 'Params(', 'put_bool', 'put_int', 'controls_allowed'))


def bsm_icons(scene):
  return [call for call in scene.calls if call[0] == 'draw_texture_ex' and call[1].name.endswith('blind_spot_left.png')]


@pytest.mark.parametrize('left,right', [(True, False), (False, True), (True, True), (False, False)])
def test_wayon_bsm_original_asset_position_and_side(scene, left, right):
  s = scene
  s.cs.leftBlindspot, s.cs.rightBlindspot = left, right
  s.renderer.draw(s.rect, s.hud)
  icons = bsm_icons(s)
  assert [call[2].x for call in icons] == ([20] if left else []) + ([408] if right else [])
  assert [call[1].flip_x for call in icons] == ([False] if left else []) + ([True] if right else [])
  assert all((call[1].width, call[1].height, call[2].y) == (108, 128, 100) for call in icons)
  assert all(tuple(call[-1]) == (255, 255, 255, 255) for call in icons)
  assert ('MAX' in texts(s)) == (not left)


@pytest.mark.parametrize('condition', ['offroad', 'invalid', 'dead', 'unseen', 'old_session'])
def test_wayon_bsm_clears_immediately_without_current_carstate(scene, condition):
  s = scene
  s.cs.leftBlindspot = s.cs.rightBlindspot = True
  s.renderer.draw(s.rect, s.hud)
  assert len(bsm_icons(s)) == 2
  if condition == 'offroad':
    s.ui.started = False
  elif condition == 'invalid':
    s.sm.valid['carState'] = False
  elif condition == 'dead':
    s.sm.alive['carState'] = False
  elif condition == 'unseen':
    s.sm.recv_frame['carState'] = 0
  else:
    s.ui.started_frame = 102
  s.calls.clear()
  s.renderer.draw(s.rect, s.hud)
  assert not bsm_icons(s) and not s.renderer._blind_spots.detected


@pytest.mark.parametrize('service', ['selfdriveState', 'carControl', 'longitudinalPlan'])
def test_bsm_does_not_depend_on_controls_or_model(scene, service):
  s = scene
  s.cs.rightBlindspot = True
  s.sm.alive[service] = False
  s.renderer.draw(s.rect, s.hud)
  assert len(bsm_icons(s)) == 1


def test_bsm_yields_to_a_critical_wheel_warning(scene):
  s = scene
  s.cs.leftBlindspot = s.cs.rightBlindspot = True
  s.hud._show_wheel_critical = True
  s.renderer.draw(s.rect, s.hud)
  assert not bsm_icons(s)
  assert s.renderer._blind_spots.detected
  wheel = next(call for call in s.calls if call[0] == 'draw_texture_pro')
  assert wheel[1].name.endswith('wheel_critical.png') and wheel[-1].a == 255


def test_bsm_session_and_ui_reset_discard_fade_memory(scene):
  s = scene
  s.cs.leftBlindspot = True
  s.renderer.draw(s.rect, s.hud)
  assert s.renderer._blind_spots.detected
  s.cs.leftBlindspot = False
  s.renderer.reset()
  assert not s.renderer._blind_spots.detected


def test_bsm_uses_original_fade_in_and_out(scene, monkeypatch):
  # Exercise the real filter, not the instantaneous HUD fixture above.
  spec = importlib.util.spec_from_file_location('_real_bsm_filter', ONROAD.parents[3] / 'common/filter_simple.py')
  filters = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(filters)
  module = sys.modules['openpilot.selfdrive.ui.mici.onroad.blind_spot_indicators']
  monkeypatch.setattr(module, 'FirstOrderFilter', filters.FirstOrderFilter)
  bsm = module.BlindSpotIndicators()
  scene.cs.leftBlindspot = True
  bsm.update()
  assert bsm._left_alpha.x == pytest.approx(.25)
  for _ in range(19):
    bsm.update()
  assert bsm._left_alpha.x > .99
  scene.cs.leftBlindspot = False
  bsm.update()
  assert .7 < bsm._left_alpha.x < .75 and bsm.detected
  for _ in range(19):
    bsm.update()
  assert not bsm.detected
