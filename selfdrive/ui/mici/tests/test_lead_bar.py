"""NewUi 1 lead bar: the marker itself (ported from WIP-FR 5325a288) and where the comma 4 model renderer puts it.

WIP-FR's Traverse radar display alignment is not on this branch, so the bar sits at the lead's own yRel."""
import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[4]

@pytest.fixture
def bar_module(monkeypatch):
  spec = importlib.util.spec_from_file_location('_test_lead_bar', ROOT / 'selfdrive/ui/mici/onroad/lead_bar.py')
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  calls = []
  raylib = SimpleNamespace(Rectangle=lambda x, y, w, h: (x, y, w, h), Color=lambda *rgba: rgba,
                          draw_rectangle_rounded=lambda *args: calls.append(args))
  monkeypatch.setattr(module, 'rl', raylib)
  return module, calls


@pytest.mark.parametrize('radar,color', [(True, (32, 216, 106)), (False, (80, 190, 255))])
@pytest.mark.parametrize('width,expected_width', [(2, 24), (64, 64), (400, 140)])
def test_colors_geometry_and_bounded_width(bar_module, radar, color, width, expected_width):
  module, calls = bar_module
  bar = module.LeadBar()
  target = module.LeadBarTarget(268, 140, width, radar)
  bar.draw(target, 0.0)
  assert not calls
  bar.draw(target, 0.18)
  assert calls == [((267 - expected_width / 2, 139, expected_width + 2, 5), 1.0, 6, (0, 0, 0, 160)),
                   ((268 - expected_width / 2, 140, expected_width, 3), 1.0, 6, (*color, 255))]


@pytest.mark.parametrize('fps', [20, 30, 60])
def test_fade_uses_elapsed_time_not_frame_count(bar_module, fps):
  module, calls = bar_module
  bar = module.LeadBar()
  target = module.LeadBarTarget(268, 140, 64, True)
  for frame in range(fps + 1):
    bar.draw(target, frame / fps)
    assert bar.alpha == pytest.approx(min(1, frame / fps / 0.18))
  for frame in range(1, fps + 1):
    bar.draw(None, 1 + frame / fps)
    assert bar.alpha == pytest.approx(max(0, 1 - frame / fps / 0.2))
  assert bar._target is None and bar._geometry is None
  count = len(calls)
  bar.draw(None, 2.05)
  assert len(calls) == count


def test_dropout_reappearance_reverses_fade_and_source_changes_color(bar_module):
  module, calls = bar_module
  bar = module.LeadBar()
  radar = module.LeadBarTarget(200, 120, 60, True, 2)
  vision = module.LeadBarTarget(200, 120, 60, False)
  bar.draw(radar, 0)
  bar.draw(radar, 0.18)
  bar.draw(None, 0.28)
  assert bar.alpha == pytest.approx(0.5)
  bar.draw(vision, 0.37)
  assert bar.alpha == pytest.approx(1)
  assert calls[-1][-1] == (*bar.VISION_COLOR, 255)
  bar.draw(None, 0.47)
  assert calls[-1][-1][:3] == bar.VISION_COLOR
  assert calls[-1][-1][3] == pytest.approx(127.5, abs=0.5)
  bar.draw(None, 0.57)
  assert bar.alpha == 0 and bar._target is None


def test_new_radar_track_never_slides_old_marker_to_new_car(bar_module):
  module, _ = bar_module
  bar = module.LeadBar()
  old = module.LeadBarTarget(160, 120, 60, True, 2)
  new = module.LeadBarTarget(360, 160, 100, True, 3)
  bar.draw(old, 0)
  bar.draw(old, 0.18)
  bar.draw(new, 0.23)
  assert bar.alpha == 0
  assert bar._geometry == (360, 160, 100)
  bar.draw(new, 0.32)
  assert bar.alpha == pytest.approx(0.5)


def test_same_target_position_is_smoothed_only_in_display(bar_module):
  module, _ = bar_module
  bar = module.LeadBar()
  old = module.LeadBarTarget(160, 120, 60, True, 2)
  new = module.LeadBarTarget(180, 130, 80, True, 2)
  bar.draw(old, 0)
  bar.draw(old, 0.18)
  bar.draw(new, 0.23)
  x, y, width = bar._geometry
  assert 160 < x < 180 and 120 < y < 130 and 60 < width < 80
  assert (new.x, new.y, new.width) == (180, 130, 80)


@pytest.mark.parametrize('now', [-1, 1, float('nan'), float('inf')])
def test_clock_discontinuity_clears_display_memory(bar_module, now):
  module, calls = bar_module
  bar = module.LeadBar()
  target = module.LeadBarTarget(268, 140, 64, True)
  bar.draw(target, 0)
  bar.draw(target, 0.18)
  calls.clear()
  bar.draw(None, now)
  assert bar.alpha == 0 and not calls
  assert bar._geometry is None


@pytest.mark.parametrize('x,y,width', [(float('nan'), 1, 60), (1, float('inf'), 60), (1, 1, float('nan')), (1, 1, 0), (1, 1, -2)])
def test_invalid_geometry_fades_out_instead_of_inventing_a_position(bar_module, x, y, width):
  module, _ = bar_module
  bar = module.LeadBar()
  target = module.LeadBarTarget(268, 140, 64, True)
  bar.draw(target, 0)
  bar.draw(target, 0.18)
  bar.draw(module.LeadBarTarget(x, y, width, False), 0.28)
  assert bar.alpha == pytest.approx(0.5)
  assert bar._geometry == (268, 140, 64)
  bar.draw(None, 0.38)
  assert bar.alpha == 0


@pytest.fixture
def lead_renderer(monkeypatch):
  pytest.importorskip('capnp')
  from openpilot.selfdrive.ui.tests.test_jetlink_badge import load_ui_modules
  existing = set(sys.modules)
  load_ui_modules(monkeypatch)
  calib = ModuleType('openpilot.selfdrive.locationd.calibrationd')
  calib.HEIGHT_INIT = np.array([1.22, 0, 0])
  monkeypatch.setitem(sys.modules, calib.__name__, calib)
  name = 'openpilot.selfdrive.ui.mici.onroad.model_renderer'
  spec = importlib.util.spec_from_file_location(name, ROOT / 'selfdrive/ui/mici/onroad/model_renderer.py')
  module = importlib.util.module_from_spec(spec)
  monkeypatch.setitem(sys.modules, name, module)
  spec.loader.exec_module(module)

  renderer = object.__new__(module.ModelRenderer)
  calls = []
  renderer._lead_bar = SimpleNamespace(draw=lambda *args: calls.append(args), reset=lambda: None)
  renderer._lead_bar_context = None
  renderer._longitudinal_control = True
  renderer._model_frame = 20
  renderer._path = module.ModelPoints(raw_points=np.array([[0, 0, 0], [20, 0, 0], [50, 0, 0]], dtype=np.float32))
  renderer._path_offset_z = 1.22
  renderer._clip_region = module.rl.Rectangle(0, 0, 536, 240)
  renderer._map_to_screen = lambda _x, y, _z: (268 - y * 20, 140)
  renderer._get_path_length_idx = lambda *_args: 1
  lead = SimpleNamespace(status=True, dRel=20, yRel=0, radar=True, radarTrackId=2)

  class SM(dict):
    pass

  sm = SM(radarState=SimpleNamespace(leadOne=lead), modelV2=None, liveCalibration=None)
  sm.valid = dict.fromkeys(sm, True)
  sm.alive = dict.fromkeys(sm, True)
  sm.recv_frame = dict.fromkeys(sm, 20)
  sm.recv_time = dict.fromkeys(sm, 99.95)
  state = SimpleNamespace(started_frame=10, started=True, new_ui=True, show_radar_info=0, sm=sm)
  monkeypatch.setattr(module, 'ui_state', state)
  monkeypatch.setattr(module.time, 'monotonic', lambda: 100.0)
  yield module, renderer, state, lead, calls
  for name in set(sys.modules) - existing:
    if name.startswith(('openpilot.selfdrive.ui.', 'openpilot.system.ui.widgets')):
      sys.modules.pop(name, None)


def draw(module, renderer, state, width=536):
  renderer._draw_lead_bar(module.rl.Rectangle(0, 0, width, 240), state.new_ui)


@pytest.mark.parametrize('show_radar_info', [0, 1, 2, 3])
@pytest.mark.parametrize('radar,track_id', [(False, 0), (True, 0), (True, 1), (True, 25)])
def test_lead_bar_projects_at_vehicle_base_independent_of_debug_info(lead_renderer, show_radar_info, radar, track_id):
  module, renderer, state, lead, calls = lead_renderer
  state.show_radar_info = show_radar_info
  lead.radar, lead.radarTrackId = radar, track_id
  draw(module, renderer, state)
  assert len(calls) == 1
  target, now = calls[0]
  assert (target.x, target.y, target.radar, target.track_id, now) == (268, 140, radar, track_id, 100)
  assert target.width == pytest.approx(38.4)


@pytest.mark.parametrize('service', ['radarState', 'modelV2', 'liveCalibration'])
@pytest.mark.parametrize('field,value', [('valid', False), ('alive', False), ('recv_frame', 0), ('recv_frame', 9)])
def test_invalid_or_previous_session_input_has_no_target(lead_renderer, service, field, value):
  module, renderer, state, _, calls = lead_renderer
  getattr(state.sm, field)[service] = value
  draw(module, renderer, state)
  assert calls == [(None, 100.0)]


@pytest.mark.parametrize('service', ['radarState', 'modelV2'])
@pytest.mark.parametrize('timestamp', [99.64, 100.01, float('nan')])
def test_stale_or_future_input_fades_out(lead_renderer, service, timestamp):
  module, renderer, state, _, calls = lead_renderer
  state.sm.recv_time[service] = timestamp
  draw(module, renderer, state)
  assert calls == [(None, 100.0)]


@pytest.mark.parametrize('field,value', [('status', False), ('dRel', 0), ('dRel', -1), ('dRel', float('nan')), ('yRel', float('nan'))])
def test_missing_or_invalid_lead_has_no_target(lead_renderer, field, value):
  module, renderer, state, lead, calls = lead_renderer
  setattr(lead, field, value)
  draw(module, renderer, state)
  assert calls == [(None, 100.0)]


@pytest.mark.parametrize('point', [None, (-1, 100), (537, 100), (268, -1), (268, 238)])
def test_offscreen_target_is_not_clamped_to_edge(lead_renderer, point):
  module, renderer, state, _, calls = lead_renderer
  renderer._map_to_screen = lambda *_args: point
  draw(module, renderer, state)
  assert calls == [(None, 100.0)]


@pytest.mark.parametrize('change', ['offroad', 'session', 'legacy', 'viewport', 'no_longitudinal'])
def test_context_change_clears_old_bar(lead_renderer, change):
  module, renderer, state, _, calls = lead_renderer
  width = 536
  draw(module, renderer, state, width)
  resets = []
  renderer._lead_bar.reset = lambda: resets.append(True)
  if change == 'offroad':
    state.started = False
  elif change == 'session':
    state.started_frame = 30
  elif change == 'legacy':
    state.new_ui = False
  elif change == 'viewport':
    width = 476
  else:
    renderer._longitudinal_control = False
  calls.clear()
  draw(module, renderer, state, width)
  assert resets
  if change in ('offroad', 'legacy', 'no_longitudinal'):
    assert not calls
  elif change == 'session':
    assert calls == [(None, 100.0)]


def test_bar_is_drawn_every_frame_also_when_the_road_layer_returns_early(lead_renderer):
  module, renderer, state, _, calls = lead_renderer
  painted = []
  renderer._paint = lambda rect, new_ui: painted.append(new_ui)
  for _ in range(4):
    renderer._render(module.rl.Rectangle(0, 0, 536, 240))
  assert painted == [True] * 4 and len(calls) == 4


def test_toggling_the_layout_requests_reprojection(lead_renderer):
  module, renderer, state, _, _ = lead_renderer
  draw(module, renderer, state)
  renderer._paint = lambda rect, new_ui: None
  renderer._transform_dirty = False
  state.new_ui = False
  renderer._render(module.rl.Rectangle(0, 0, 536, 240))
  assert renderer._transform_dirty


@pytest.mark.parametrize('new_ui', [False, True])
def test_carrot_lead_box_remains_only_in_the_carrot_layout(lead_renderer, new_ui):
  module, renderer, state, _, _ = lead_renderer
  state.new_ui = new_ui
  state.status = module.UIStatus.DISENGAGED
  sm = state.sm
  sm['carOutput'] = SimpleNamespace(actuatorsOutput=SimpleNamespace(torque=0))
  sm['selfdriveState'] = SimpleNamespace(experimentalMode=False)
  sm['liveCalibration'] = SimpleNamespace(height=[1.22])
  sm['modelV2'] = SimpleNamespace()
  sm.updated = {'carParams': False, 'modelV2': False, 'radarState': True}
  renderer._transform_dirty = False
  renderer._torque_filter = SimpleNamespace(update=lambda *_args: None)
  updates, draws = [], []
  renderer._update_model = lambda *_args: None
  renderer._update_leads_carrot = lambda *_args: updates.append(True)
  renderer._draw_lead_indicator = lambda: draws.append(True)
  renderer._draw_lane_lines = lambda: None
  renderer._paint(module.rl.Rectangle(0, 0, 536, 240), new_ui)
  assert len(updates) == len(draws) == (0 if new_ui else 1)


@pytest.mark.parametrize('distance,lateral', [(10, 0), (20, -1), (20, 1), (50, 0), (90, 0)])
def test_actual_projection_anchors_bar_to_the_carrot_box_bottom(lead_renderer, distance, lateral):
  module, renderer, state, lead, calls = lead_renderer
  lead.dRel, lead.yRel = distance, lateral
  renderer._car_space_transform = np.array([[268, -400, 0], [100, 0, 400], [1, 0, 0]], dtype=np.float32)
  renderer._map_to_screen = module.ModelRenderer._map_to_screen.__get__(renderer)
  renderer._get_path_length_idx = module.ModelRenderer._get_path_length_idx
  renderer._rect = module.rl.Rectangle(0, 0, 536, 240)
  renderer._lead_pt_filt = [None, None]
  renderer._update_leads_carrot(state.sm['radarState'], renderer._path.raw_points[:, 0])
  old_rect = renderer._lead_vehicles[0].rect
  draw(module, renderer, state)
  target, _ = calls[-1]
  assert target is not None
  assert target.x == pytest.approx((old_rect[2][0] + old_rect[3][0]) / 2)
  assert target.y == pytest.approx(old_rect[2][1])
  assert target.width == pytest.approx(400 * 2.4 / distance * 0.8)
