"""Minimum-distance clamp (min_distance_clamp.py, aabc88/openpilot ej as WIP-FR ported it; WIP-FR's tests, user request
2026-11-19 for cluster_egpu). Signs as carrot logs them: model y + right, curvature + right."""
import ast
import importlib
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

import numpy as np
import pytest

from openpilot.selfdrive.controls.lib import min_distance_clamp as mdc
from openpilot.selfdrive.controls.lib.drive_helpers import MAX_LATERAL_JERK, clip_curvature, lateral_jerk_limit

ROOT = Path(__file__).resolve().parents[3]
X = np.linspace(0.0, 100.0, 33)


def line(y):
  return NS(x=list(X), y=[float(y)] * len(X), t=list(np.linspace(0, 10, 33)))


def model(path_y=0.0, left=-1.75, right=1.75, l_prob=0.9, r_prob=0.9, left_edge=-3.5, right_edge=3.5, edge_std=0.2):
  return NS(position=NS(x=list(X), y=[float(path_y)] * len(X)),
            laneLines=[line(-5.25), line(left), line(right), line(5.25)],
            laneLineProbs=[0.5, l_prob, r_prob, 0.5],
            roadEdges=[line(left_edge), line(right_edge)], roadEdgeStds=[edge_std, edge_std])


class Clock:
  def __init__(self):
    self.t = 100.0

  def __call__(self, dt=0.01):
    self.t += dt
    return self.t


DEFAULT_SETTINGS = mdc.ClampSettings()


def run(c, md, v, seconds, settings=DEFAULT_SETTINGS, lane_changing=False, clock=None):
  clock = clock or Clock()
  out = 0.0
  for _ in range(int(round(seconds / 0.01))):
    out = c.update(md, v, lane_changing, settings, now=clock())
  return out, clock


def test_centred_in_the_lane_changes_nothing():
  c = mdc.ClampSteerCorrector()
  out, _ = run(c, model(), 25.0, 2.0)
  assert out == 0.0 and not c.alert_active


def test_too_close_to_the_left_line_steers_right_and_eases_in():
  c = mdc.ClampSteerCorrector()
  md = model(path_y=-1.75 + 0.95)            # 95 cm from the left line, 40 cm inside the 1.35 m bound
  clock = Clock()
  first = c.update(md, 25.0, False, mdc.ClampSettings(), now=clock())
  assert first == 0.0                        # starts from zero ...
  out, _ = run(c, md, 25.0, 0.1, clock=clock)
  assert 0 < out < 0.002                     # ... and eases in (slew limit) ...
  out, _ = run(c, md, 25.0, 1.5, clock=clock)
  ahead = 25.0 * mdc.LOOKAHEAD_TIME
  expected = min(2 * 0.40 / ahead ** 2, mdc.MAX_LAT_ACCEL / 25.0 ** 2)
  assert out == pytest.approx(expected, rel=1e-3)   # + = right, away from the left line (pure pursuit, capped)
  assert c.alert_active


def test_too_close_to_the_right_line_steers_left():
  c = mdc.ClampSteerCorrector()
  out, _ = run(c, model(path_y=1.75 - 0.95), 25.0, 2.0)
  assert out < 0


def test_deadband_ignores_small_violations_until_twenty_centimetres():
  c = mdc.ClampSteerCorrector()
  out, clock = run(c, model(path_y=-1.75 + 1.35 - 0.15), 25.0, 2.0)   # 15 cm inside the bound
  assert out == 0.0
  out, clock = run(c, model(path_y=-1.75 + 1.35 - 0.25), 25.0, 2.0, clock=clock)   # 25 cm: on
  assert out > 0
  out, clock = run(c, model(path_y=-1.75 + 1.35 - 0.12), 25.0, 0.05, clock=clock)  # 12 cm: still on (hysteresis)
  assert c.correcting
  out, clock = run(c, model(path_y=-1.75 + 1.35 - 0.05), 25.0, 2.0, clock=clock)   # under 10 cm: off, eased out
  assert not c.correcting and out == 0.0


def test_lane_change_and_turn_signal_hold_it_off_for_two_seconds_after():
  c = mdc.ClampSteerCorrector()
  md = model(path_y=-1.75 + 0.9)
  out, clock = run(c, md, 25.0, 1.0, lane_changing=True)
  assert out == 0.0
  out, clock = run(c, md, 25.0, 1.9, clock=clock)
  assert out == 0.0                          # still in the hold-off
  out, clock = run(c, md, 25.0, 1.0, clock=clock)
  assert out > 0


def test_off_switches_and_low_speed():
  md = model(path_y=-1.75 + 0.9)
  assert run(mdc.ClampSteerCorrector(), md, 25.0, 2.0, settings=mdc.ClampSettings(enabled=False))[0] == 0.0
  assert run(mdc.ClampSteerCorrector(), md, 25.0, 2.0, settings=mdc.ClampSettings(gain=0.0))[0] == 0.0
  assert run(mdc.ClampSteerCorrector(), md, 1.0, 2.0)[0] == 0.0           # 3.6 km/h: faded out
  c = mdc.ClampSteerCorrector()
  run(c, md, 25.0, 2.0, settings=mdc.ClampSettings(alert=False))
  assert not c.alert_active and c.correction > 0                          # alert off: the correction still works


def test_unseen_line_falls_back_to_a_sure_road_edge_only():
  md = model(path_y=-3.5 + 0.8, l_prob=0.1, left_edge=-3.5, edge_std=0.2)   # no left line, 80 cm from the edge
  assert run(mdc.ClampSteerCorrector(), md, 25.0, 2.0)[0] > 0
  md = model(path_y=-3.5 + 0.8, l_prob=0.1, left_edge=-3.5, edge_std=0.8)   # unsure edge: no bound
  assert run(mdc.ClampSteerCorrector(), md, 25.0, 2.0)[0] == 0.0


def test_narrow_lane_goes_to_its_middle():
  lo, hi = mdc.bounds(-1.2, 1.2, 0.0, 0.0, True, True, 9.0, 9.0, mdc.ClampSettings())
  assert lo > hi and float(mdc.clamp_target(0.5, lo, hi)) == pytest.approx(0.0)


def test_bad_model_data_gives_no_correction():
  c = mdc.ClampSteerCorrector()
  md = model(path_y=-1.0)
  md.position = NS(x=[], y=[])
  assert run(c, md, 25.0, 1.0)[0] == 0.0
  md = model(path_y=float('nan'))
  assert run(mdc.ClampSteerCorrector(), md, 25.0, 1.0)[0] == 0.0


def test_alert_cools_down_for_five_seconds():
  c = mdc.ClampSteerCorrector()
  clock = Clock()
  run(c, model(path_y=-1.75 + 0.9), 25.0, 2.0, clock=clock)
  assert c.alert_active
  run(c, model(), 25.0, 1.0, clock=clock)
  assert not c.alert_active
  run(c, model(path_y=-1.75 + 0.9), 25.0, 2.0, clock=clock)
  assert not c.alert_active                  # within the cooldown: the correction works, no new ding
  run(c, model(path_y=-1.75 + 0.9), 25.0, 3.0, clock=clock)
  assert c.alert_active


class FakeParams:
  def __init__(self, values=None, raises=False):
    self.values, self.raises = values or {}, raises

  def get(self, key, return_default=False):
    if self.raises:
      raise RuntimeError("UnknownKeyName")
    return self.values.get(key)


def test_settings_read_with_safe_fallbacks():
  s = mdc.read_clamp_settings(FakeParams({"ClampEnable": False, "ClampMinDistLane": 150, "ClampMinDistEdge": 100,
                                          "ClampSteerGain": 15, "ClampSteerAlert": False}))
  assert s == mdc.ClampSettings(False, 1.5, 1.0, 1.5, False)
  assert mdc.read_clamp_settings(FakeParams(raises=True)) == mdc.ClampSettings()
  bad = mdc.read_clamp_settings(FakeParams({"ClampMinDistLane": 5000, "ClampSteerGain": -3, "ClampEnable": "x"}))
  assert bad == mdc.ClampSettings()


@pytest.mark.parametrize("setting, expected", [(20, 2.0), (50, 5.0), (75, 7.5), (100, 10.0), (19, MAX_LATERAL_JERK),
                                               (101, MAX_LATERAL_JERK), (None, MAX_LATERAL_JERK), (True, MAX_LATERAL_JERK),
                                               ("60", MAX_LATERAL_JERK), (60.0, MAX_LATERAL_JERK)])
def test_jerk_limit_setting(setting, expected):
  assert lateral_jerk_limit(setting) == pytest.approx(expected)


def test_jerk_limit_sets_how_fast_curvature_may_change():
  v = 20.0
  for jerk in (2.0, 5.0, 8.0):
    out, _ = clip_curvature(v, 0.0, 0.05, 0.0, max_lat_jerk=jerk)
    assert out == pytest.approx(jerk / v ** 2 * 0.01)
  assert clip_curvature(v, 0.0, 0.05, 0.0)[0] == pytest.approx(MAX_LATERAL_JERK / v ** 2 * 0.01)   # stock default


def lane_planner():
  if 'openpilot.common.params_pyx' not in sys.modules:
    try:
      importlib.import_module('openpilot.common.params_pyx')
    except ImportError:
      stub = ModuleType('openpilot.common.params')
      stub.Params = lambda: FakeParams()
      sys.modules['openpilot.common.params'] = stub
  return importlib.import_module('openpilot.selfdrive.controls.lib.lane_planner_2').LanePlanner()


def test_lane_mode_path_clamp_bounds_only_the_violation():
  lp = lane_planner()
  lp.ll_x, lp.lll_y, lp.rll_y = X, np.full(33, -1.75), np.full(33, 1.75)
  lp.l_prob = lp.r_prob = 0.9
  lp.edges_valid = False
  path = np.stack([X, np.linspace(0.0, -0.9, 33), np.zeros(33)], axis=1)   # drifting left toward the line
  out = lp.apply_min_distance_clamp(path.copy(), 25.0)
  assert np.allclose(out[:10, 1], path[:10, 1])                           # untouched where it keeps its distance
  assert out[:, 1].min() == pytest.approx(-1.75 + 1.35)                   # pushed back to the bound, no further
  assert lp.clamp_sides == "L" and lp.clamp_max_correction == pytest.approx(0.9 - 0.4)
  lp.lane_change_multiplier = 0.0                                         # during a lane change: off
  assert np.allclose(lp.apply_min_distance_clamp(path.copy(), 25.0)[:, 1], path[:, 1])
  lp.lane_change_multiplier = 1.0
  lp.clamp_settings = mdc.ClampSettings(enabled=False)
  assert np.allclose(lp.apply_min_distance_clamp(path.copy(), 25.0)[:, 1], path[:, 1])


def test_controlsd_reads_the_settings_once_a_second_not_per_cycle():
  src = (ROOT / "selfdrive" / "controls" / "controlsd.py").read_text()
  tree = ast.parse(src)
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Controls")
  calls = {}
  for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
    for node in ast.walk(fn):
      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
          'read_clamp_settings', 'read_lat_jerk_limit'):
        calls.setdefault(fn.name, []).append(node.func.attr)
  assert set(calls) == {'__init__', 'update'}                             # never from state_control (100 Hz)
  update = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'update')
  assert "self.sm.frame % 100 == 0" in ast.get_source_segment(src, update)
  state_control = ast.get_source_segment(src, next(
    n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'state_control'))
  assert not any(key in state_control for key in ('"Clamp', '"LatJerkMax"', 'read_clamp_settings('))


# ---- Codex cross-review (2026-11-18) R1-R7

def settled(md=None, v=25.0):
  c = mdc.ClampSteerCorrector()
  out, clock = run(c, md or model(path_y=-1.75 + 0.9), v, 2.0)
  assert out > 0.001
  return c, clock


@pytest.mark.parametrize("change", ["lane_change", "disabled", "gain0", "unusable", "bad_data", "slow"])
def test_protective_stops_are_immediate_not_eased(change):
  c, clock = settled()
  md, v, settings, lane_changing, usable = model(path_y=-1.75 + 0.9), 25.0, mdc.ClampSettings(), False, True
  if change == "lane_change":
    lane_changing = True
  elif change == "disabled":
    settings = mdc.ClampSettings(enabled=False)
  elif change == "gain0":
    settings = mdc.ClampSettings(gain=0.0)
  elif change == "unusable":
    usable = False
  elif change == "bad_data":
    md.position = NS(x=[], y=[])
  elif change == "slow":
    v = 1.0
  out = c.update(md, v, lane_changing, settings, now=clock(), usable=usable)
  assert out == 0.0 and not c.alert_active                # the very next cycle (R1, R3)


def test_lane_change_outside_laneless_still_holds_it_off_and_ends_the_alert():
  c, clock = settled()
  assert c.alert_active
  md = model(path_y=-1.75 + 0.9)
  for _ in range(30):                                     # lane mode / not steering, a lane change meanwhile (R4)
    assert c.update(md, 25.0, True, mdc.ClampSettings(), now=clock(), usable=False) == 0.0
  assert not c.alert_active
  out, clock = run(c, md, 25.0, 1.9, clock=clock)         # back in laneless: still in the 2 s hold-off
  assert out == 0.0
  out, clock = run(c, md, 25.0, 1.0, clock=clock)
  assert out > 0 and not c.alert_active                   # correcting again, but within the 5 s alert cooldown


def test_output_stays_under_the_current_speeds_cap_while_speeding_up():
  c = mdc.ClampSteerCorrector()
  md = model(path_y=-1.75 + 0.35)                         # a 1 m violation: the target sits at the cap
  v, clock = 9.0, Clock()
  for _ in range(300):
    c.update(md, v, False, mdc.ClampSettings(), now=clock())
  for _ in range(20):                                     # 3 m/s^2 for 0.2 s (R5)
    v += 0.03
    out = c.update(md, v, False, mdc.ClampSettings(), now=clock())
    assert abs(out) <= mdc.curvature_cap(v) + 1e-12


def test_one_bad_side_does_not_take_the_other_sides_bound():
  md = model(path_y=-1.75 + 0.95, right=float('nan'))     # a NaN right line (R6)
  assert run(mdc.ClampSteerCorrector(), md, 25.0, 2.0)[0] > 0
  md = model(path_y=-1.75 + 0.95)
  md.roadEdges = [NS(x=[], y=[0.0, 0.0]), NS(x=[], y=[0.0, 0.0])]   # unusable edges, not needed with both lines
  assert run(mdc.ClampSteerCorrector(), md, 25.0, 2.0)[0] > 0


def test_lane_mode_path_clamp_rests_for_turn_signals_and_two_seconds_after():
  lp = lane_planner()
  lp.ll_x, lp.lll_y, lp.rll_y = X, np.full(33, -1.75), np.full(33, 1.75)
  lp.l_prob = lp.r_prob = 0.9
  lp.edges_valid = False
  path = np.stack([X, np.full(33, -0.8), np.zeros(33)], axis=1)
  t = 1000.0
  assert np.allclose(lp.apply_min_distance_clamp(path.copy(), 25.0, True, now=t)[:, 1], path[:, 1])   # signal on (R2)
  assert np.allclose(lp.apply_min_distance_clamp(path.copy(), 25.0, False, now=t + 1.9)[:, 1], path[:, 1])
  assert lp.apply_min_distance_clamp(path.copy(), 25.0, False, now=t + 2.1)[:, 1].min() > -0.8   # clamps again


def test_selfdrived_ignores_a_stale_clamp_flag():
  src = (ROOT / "selfdrive" / "selfdrived" / "selfdrived.py").read_text()
  line = next(ln for ln in src.splitlines() if "clampSteerActive" in ln and ln.strip().startswith("if "))
  assert "valid['controlsState']" in line and "alive['controlsState']" in line          # R7


# ---- Codex re-check (2026-11-18): the two remaining data-error cases

def test_both_boundaries_broken_stops_at_once():
  for broken in ("nan", "empty_x"):
    c, clock = settled()
    md = model(path_y=-1.75 + 0.9)
    if broken == "nan":
      md.laneLines[1].y = [float('nan')] * 33
      md.laneLines[2].y = [float('nan')] * 33
    else:
      md.laneLines[1].x = []
      md.laneLines[2].x = []
    md.roadEdgeStds = [0.9, 0.9]                       # no sure edge to fall back on
    assert c.update(md, 25.0, False, mdc.ClampSettings(), now=clock()) == 0.0, broken
    assert not c.alert_active


def test_one_sides_empty_line_keeps_the_other_sides_line():
  md = model(path_y=-1.75 + 0.95, edge_std=0.9)
  md.laneLines[2].y = []                               # the right line's array is empty
  assert run(mdc.ClampSteerCorrector(), md, 25.0, 2.0)[0] > 0


def test_a_broken_line_keeps_blocking_the_road_edge_while_it_stays_broken():
  for side in (1, 2):
    c = mdc.ClampSteerCorrector()
    out, clock = run(c, model(path_y=-1.75 + 0.9 if side == 1 else 1.75 - 0.9, left_edge=-2.0, right_edge=2.0), 25.0, 2.0)
    assert abs(out) > 0.001
    md = model(path_y=-1.75 + 0.9 if side == 1 else 1.75 - 0.9, left_edge=-2.0, right_edge=2.0)
    md.laneLines[1].x = []
    md.laneLines[2].x = []
    for _ in range(200):                                  # 2 s of the same broken message (Codex re-check 3)
      assert c.update(md, 25.0, False, mdc.ClampSettings(), now=clock()) == 0.0
    good = model(path_y=-1.75 + 0.9 if side == 1 else 1.75 - 0.9, left_edge=-2.0, right_edge=2.0)
    out, clock = run(c, good, 25.0, 2.0, clock=clock)     # the lines come back: it corrects again
    assert out != 0.0
