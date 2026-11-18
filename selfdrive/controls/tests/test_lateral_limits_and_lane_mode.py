"""ej's lateral limits (LatAccelMaxLow / LatAccelMaxHigh / LatJerkMax), laneless in sharp curves (LanelessCurvature) and
lane mode with a lead car (LaneModeWithLead); user request 2026-11-19 for cluster_egpu."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib import lane_mode_switch as lms
from openpilot.selfdrive.controls.lib.drive_helpers import (MAX_LATERAL_ACCEL_NO_ROLL, MAX_LATERAL_ACCEL_NO_ROLL_LOW_SPEED,
                                                           MAX_LATERAL_JERK, MAX_CURVATURE, clip_curvature,
                                                           lateral_accel_limits, lateral_jerk_limit)

ROOT = Path(__file__).resolve().parents[3]


# ---- lateral acceleration / jerk limits (ej 1b670d9b)

@pytest.mark.parametrize("low, high, expected", [
  (45, 30, (4.5, 3.0)), (20, 60, (2.0, 6.0)), (60, 20, (6.0, 2.0)),
  (19, 61, (MAX_LATERAL_ACCEL_NO_ROLL_LOW_SPEED, MAX_LATERAL_ACCEL_NO_ROLL)),
  (None, None, (MAX_LATERAL_ACCEL_NO_ROLL_LOW_SPEED, MAX_LATERAL_ACCEL_NO_ROLL)),
  (True, "40", (MAX_LATERAL_ACCEL_NO_ROLL_LOW_SPEED, MAX_LATERAL_ACCEL_NO_ROLL)),
  (40.0, 35, (MAX_LATERAL_ACCEL_NO_ROLL_LOW_SPEED, 3.5)),
])
def test_lateral_accel_settings(low, high, expected):
  assert lateral_accel_limits(low, high) == pytest.approx(expected)


def test_defaults_are_the_stock_constants():
  assert lateral_accel_limits(45, 30) == (MAX_LATERAL_ACCEL_NO_ROLL_LOW_SPEED, MAX_LATERAL_ACCEL_NO_ROLL)
  assert lateral_jerk_limit(50) == MAX_LATERAL_JERK


def accel_cap(v, **limits):
  """The largest lateral acceleration clip_curvature lets through at v (no roll, no jerk limit in the way)."""
  out, limited = clip_curvature(v, 1.0, 1.0, 0.0, **limits)   # prev = new: the jerk limit never binds
  assert limited
  return out * v ** 2


@pytest.mark.parametrize("kph, expected", [(30, 4.5), (80, 4.5), (100, 3.75), (120, 3.0), (150, 3.0)])
def test_accel_limit_runs_from_low_at_80_to_high_at_120_kph(kph, expected):
  v = kph / 3.6
  if MAX_CURVATURE * v ** 2 < expected:
    pytest.skip("MAX_CURVATURE binds first at this speed")
  assert accel_cap(v) == pytest.approx(expected)


def test_accel_limits_follow_the_settings():
  assert accel_cap(70 / 3.6, max_lat_accel_low=2.5, max_lat_accel_high=2.0) == pytest.approx(2.5)
  assert accel_cap(130 / 3.6, max_lat_accel_low=2.5, max_lat_accel_high=5.5) == pytest.approx(5.5)
  assert accel_cap(100 / 3.6, max_lat_accel_low=2.0, max_lat_accel_high=6.0) == pytest.approx(4.0)


def test_jerk_limit_sets_the_curvature_rate():
  v = 20.0
  for jerk in (2.0, 5.0, 10.0):
    out, _ = clip_curvature(v, 0.0, 0.05, 0.0, max_lat_jerk=jerk)
    assert out == pytest.approx(jerk / v ** 2 * DT_CTRL)


def controls_source():
  src = (ROOT / "selfdrive" / "controls" / "controlsd.py").read_text()
  tree = ast.parse(src)
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Controls")
  fns = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
  return src, fns


def test_controlsd_reads_the_new_settings_at_1hz_and_ramps_the_accel_limits():
  src, fns = controls_source()
  readers = ('read_lat_accel_limits', 'read_lat_jerk_limit', 'read_clamp_settings', 'read_laneless_curvature')
  calls = {}
  for name, fn in fns.items():
    for node in ast.walk(fn):
      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in readers:
        calls.setdefault(name, set()).add(node.func.attr)
  assert calls == {'__init__': set(readers), 'update': set(readers)}   # never from state_control (100 Hz)
  update = ast.get_source_segment(src, fns['update'])
  assert "self.sm.frame % 100 == 0" in update and "LAT_ACCEL_LIMIT_RATE * DT_CTRL" in update
  state_control = ast.get_source_segment(src, fns['state_control'])
  assert "max_lat_accel_low=self.lat_accel_low" in state_control
  assert "max_lat_accel_high=self.lat_accel_high" in state_control and "max_lat_jerk=self.lat_jerk_limit" in state_control
  for key in ('"LatAccelMax', '"LatJerkMax"', '"Clamp', '"LanelessCurvature"', '"LaneModeWithLead"'):
    assert key not in state_control


def test_accel_limit_ramp_runs_the_production_update_lines():
  src, fns = controls_source()
  update = fns['update']
  # the ramp lines (step, low, high), run on their own
  lines = [ast.get_source_segment(src, n) for n in update.body
           if isinstance(n, (ast.Assign, ast.AugAssign)) and 'read_' not in ast.get_source_segment(src, n)
           and ('lat_accel_' in ast.get_source_segment(src, n) or 'LAT_ACCEL_LIMIT_RATE' in ast.get_source_segment(src, n))]
  assert len(lines) == 3   # step, low, high
  ns = {"np": np, "DT_CTRL": DT_CTRL, "LAT_ACCEL_LIMIT_RATE": 0.5}
  self = NS(lat_accel_low=4.5, lat_accel_high=3.0, lat_accel_low_target=3.0, lat_accel_high_target=6.0)
  ns["self"] = self
  for _ in range(100):   # one second
    for line in lines:
      exec(line, ns)
  assert self.lat_accel_low == pytest.approx(4.0) and self.lat_accel_high == pytest.approx(3.5)
  for _ in range(1000):
    for line in lines:
      exec(line, ns)
  assert self.lat_accel_low == pytest.approx(3.0) and self.lat_accel_high == pytest.approx(6.0)


# ---- LaneModeWithLead

@pytest.mark.parametrize("value, expected", [(True, True), (1, True), (False, False), (0, False), (None, False),
                                             ("1", False), (2, False)])
def test_lead_setting(value, expected):
  assert lms.lead_lane_mode_setting(value) is expected


def test_lead_lane_mode_turns_on_after_half_a_second_and_off_after_two():
  m = lms.LeadLaneMode()
  t = 100.0
  assert not m.update(True, True, t)
  assert not m.update(True, True, t + 0.4)
  assert m.update(True, True, t + 0.5)
  assert m.update(True, False, t + 1.0)          # the lead dropped out ...
  assert m.update(True, True, t + 1.2)           # ... for a moment: still on, and the off timer restarts
  assert m.update(True, False, t + 1.3)
  assert m.update(True, False, t + 3.2)
  assert not m.update(True, False, t + 3.3)      # two seconds without one
  assert not m.update(True, True, t + 3.4)       # and it needs half a second again
  assert m.update(True, True, t + 3.9)
  assert not m.update(False, True, t + 4.0)      # the toggle off: off at once
  assert not m.update(True, True, t + 4.1)


def test_lateral_planner_ors_lead_lane_mode_into_the_speed_rule():
  src = (ROOT / "selfdrive" / "controls" / "lib" / "lateral_planner.py").read_text()
  assert "self.useLaneLineMode = self.speed_lane_mode or (lead_lane_mode and not self.laneless_only)" in src
  assert "lead = bool(sm.alive['radarState'] and radar.leadOne.status)" in src
  # the speed rule itself is unchanged: off when UseLaneLineSpeed is 0 or the model-speed guard is on
  assert "if self.useLaneLineSpeedApply == 0 or self.laneless_only:\n      self.speed_lane_mode = False" in src
  for speed_mode, lead_mode, laneless_only in np.ndindex(2, 2, 2):
    speed_mode, lead_mode, laneless_only = bool(speed_mode), bool(lead_mode), bool(laneless_only)
    if laneless_only:
      speed_mode = False   # the speed rule is off under the guard (above)
    use = speed_mode or (lead_mode and not laneless_only)
    assert use == ((speed_mode or lead_mode) and not laneless_only)


# ---- LanelessCurvature

@pytest.mark.parametrize("value, expected", [(0, 0.0), (1, 1e-4), (40, 0.004), (200, 0.02), (201, 0.0), (-5, 0.0),
                                             (None, 0.0), (True, 0.0), (40.0, 0.0)])
def test_curvature_setting(value, expected):
  assert lms.laneless_curvature_setting(value) == pytest.approx(expected)


def model(desired=0.0, plan=0.0, speed=20.0, t_max=10.0):
  t = list(np.linspace(0.0, t_max, 33))
  return NS(action=NS(desiredCurvature=desired),
            orientationRate=NS(t=t, z=[plan * speed] * 33), velocity=NS(x=[speed] * 33))


def test_model_curvature_takes_the_sharper_of_now_and_the_next_second():
  assert lms.model_curvature(model(desired=0.002, plan=0.001)) == pytest.approx(0.002)
  assert lms.model_curvature(model(desired=-0.001, plan=-0.005)) == pytest.approx(0.005)
  md = model(desired=0.001)
  md.orientationRate.z = [0.0] * 5 + [0.2] * 28      # a bend 1.5 s ahead does not count yet
  assert lms.model_curvature(md) == pytest.approx(0.001)
  md = model(desired=0.001, plan=0.01, speed=0.5)     # yaw rate at a crawl says nothing
  assert lms.model_curvature(md) == pytest.approx(0.001)
  assert lms.model_curvature(NS()) == 0.0
  md = model(desired=float('nan'), plan=0.003)
  assert lms.model_curvature(md) == pytest.approx(0.003)


def test_curve_laneless_hysteresis():
  c = lms.CurveLaneless()
  thr, t = 0.004, 50.0
  assert not c.update(thr, model(desired=0.0039), t)
  assert c.update(thr, model(desired=0.0041), t + 0.1)
  assert c.update(thr, model(desired=0.0033), t + 0.2)     # above 80%: stays laneless
  assert c.update(thr, model(desired=0.003), t + 0.3)      # under 80% ...
  assert c.update(thr, model(desired=0.0035), t + 0.9)     # ... back above: the second restarts
  assert c.update(thr, model(desired=0.003), t + 1.0)
  assert c.update(thr, model(desired=0.003), t + 1.9)
  assert not c.update(thr, model(desired=0.003), t + 2.0)  # a full second under 80%: lane mode again
  assert c.update(thr, model(desired=0.005), t + 2.1)
  assert not c.update(0.0, model(desired=0.05), t + 2.2)   # setting off: never laneless


def test_controlsd_turns_lane_mode_off_in_sharp_curves_only():
  src, fns = controls_source()
  state_control = ast.get_source_segment(src, fns['state_control'])
  assert "curve_laneless = self.curve_laneless.update(self.laneless_curvature, model_v2, time.monotonic())" in state_control
  assert "and not curve_laneless)" in state_control
  assert 'curve_speed_abs > self.params.get_int("UseLaneLineCurveSpeed")' in state_control   # the old rule stays


def test_settings_reader_never_raises():
  class P:
    def __init__(self, values=None, raises=False):
      self.values, self.raises = values or {}, raises

    def get(self, key, return_default=False):
      if self.raises:
        raise RuntimeError("UnknownKeyName")
      return self.values.get(key)
  assert lms.read_lane_mode_settings(P({"LaneModeWithLead": True, "LanelessCurvature": 50})) == (True, pytest.approx(0.005))
  assert lms.read_lane_mode_settings(P(raises=True)) == (False, 0.0)
  assert lms.read_lane_mode_settings(P()) == (False, 0.0)
