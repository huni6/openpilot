"""Minimum-distance clamp: keeps the car a minimum distance away from the lane lines, or from the road edge on a side
where no line is seen.

Ported from aabc88/openpilot `ej` (2026-11-17, commits be252819 18c0344b 399efba7 916a79f0 6a06a6ce 2c50e890) by way
of leehyuk1108/carrotpilot WIP-FR (d3c44125 ca180ae3 2356f130 1939d1fb, which fixed what Codex's reviews found in ej's
version); the module is WIP-FR's as it is.

- Lane mode: LanePlanner.apply_min_distance_clamp (lane_planner_2.py) bounds the final path the lateral planner
  follows; it only moves the path where it breaks the bound, and only by as much.
- Laneless: steering follows the model's own curvature, so the planner's path never reaches the wheel. controlsd adds
  ClampSteerCorrector's curvature to the model's: zero unless the model's plan, one second ahead, comes closer to a
  line than the bound (beyond a 20 cm deadband), and then a pure-pursuit nudge capped at 1.5 m/s^2 and 0.02 1/m.

Settings are read by controlsd once a second and by the lateral planner with its other settings (2 Hz), never per
control cycle (ej read eight Params files every 10 ms in controlsd). Signs (as carrot logs them, checked with GPS heading): model y is + to the right
(laneLines[1] is the left line, negative), curvature is + for a right turn.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

PROB_ON = 0.5           # a lane line counts once its probability goes above this ...
PROB_OFF = 0.3          # ... and stops counting only below this (no flicker at the boundary)
EDGE_STD_MAX = 0.5      # the road edge is used only when the model is this sure of it (std, m)
EDGE_STD_MARGIN = 0.4   # and taken this many of its stds inward (as lane_planner_2 parses the edges)
FADE_IN_KPH = (5.0, 10.0)   # off below 5 km/h, full from 10 km/h (crosswalks, parking lines)

DEFAULT_MIN_LANE_M = 1.35   # car centre to a lane line (lane_planner_2 KEEP_MIN_DISTANCE_FROM_LANE)
DEFAULT_MIN_EDGE_M = 1.15   # car centre to the road edge (KEEP_MIN_DISTANCE_FROM_EDGELANE)
MIN_LANE_RANGE_CM = (60, 200)
MIN_EDGE_RANGE_CM = (40, 180)
GAIN_RANGE = (0, 20)        # x0.1
GAIN_MAX = 2.0

# laneless steering correction
LOOKAHEAD_TIME = 1.0                     # s: where it looks (speed x 1 s) ...
LOOKAHEAD_MIN, LOOKAHEAD_MAX = 5.0, 30.0   # ... within these distances, m
MAX_LAT_ACCEL = 1.5                      # m/s^2 the correction itself may add
MAX_CURVATURE = 0.02                     # 1/m, so it never oversteers at low speed
DEADBAND_ON, DEADBAND_OFF = 0.20, 0.10   # m: corrects once the plan is 20 cm past the bound, until it is under 10 cm
SLEW_RATE = 0.01                         # 1/m/s: from 0 to a full correction in about half a second
LANE_CHANGE_HOLDOFF_S = 2.0              # no correction for this long after a lane change (the car is still settling)
GAP_RESET_S = 0.5                        # not called for this long (lane mode, not steering): start again from 0
ALERT_ON, ALERT_OFF = 0.001, 0.0005      # 1/m: about 0.28 m/s^2 at 60 km/h, a correction the driver feels
ALERT_COOLDOWN_S = 5.0                   # no new ding for this long after one ends


@dataclass(frozen=True)
class ClampSettings:
  enabled: bool = True
  min_lane: float = DEFAULT_MIN_LANE_M
  min_edge: float = DEFAULT_MIN_EDGE_M
  gain: float = 1.0
  alert: bool = True


def _int_setting(value, lo: int, hi: int):
  if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
    return None
  return value


def read_clamp_settings(params) -> ClampSettings:
  """The clamp settings, read once a second by the caller. Missing, unregistered (an old native registry) or corrupt
  values fall back to the defaults, so the clamp neither switches itself off nor dies."""
  def get(key):
    try:
      return params.get(key, return_default=True)
    except Exception:
      return None
  enabled, alert = get("ClampEnable"), get("ClampSteerAlert")
  lane = _int_setting(get("ClampMinDistLane"), *MIN_LANE_RANGE_CM)
  edge = _int_setting(get("ClampMinDistEdge"), *MIN_EDGE_RANGE_CM)
  gain = _int_setting(get("ClampSteerGain"), *GAIN_RANGE)
  return ClampSettings(
    enabled=enabled if isinstance(enabled, bool) else True,
    min_lane=DEFAULT_MIN_LANE_M if lane is None else lane / 100.0,
    min_edge=DEFAULT_MIN_EDGE_M if edge is None else edge / 100.0,
    gain=1.0 if gain is None else min(gain / 10.0, GAIN_MAX),
    alert=alert if isinstance(alert, bool) else True,
  )


def fade_in(v_ego: float) -> float:
  return float(np.interp(v_ego * 3.6, FADE_IN_KPH, [0.0, 1.0]))


def bounds(left_line, right_line, left_edge, right_edge, left_on: bool, right_on: bool, left_std: float,
           right_std: float, settings: ClampSettings):
  """(lo, hi): the plan must stay right of lo and left of hi (model y, + right). A seen line wins over the road edge
  on its side; an unsure edge (std above EDGE_STD_MAX) gives no bound. Works on scalars or arrays."""
  lo = left_line + settings.min_lane if left_on else (
    left_edge + settings.min_edge if left_std < EDGE_STD_MAX else -np.inf)
  hi = right_line - settings.min_lane if right_on else (
    right_edge - settings.min_edge if right_std < EDGE_STD_MAX else np.inf)
  return lo, hi


def clamp_target(path_y, lo, hi):
  """Where the plan should be: as it is inside the bounds, pushed back by the violation outside them, and halfway
  between them when the lane is too narrow for both (lo > hi)."""
  target = np.clip(path_y, lo, hi)
  return np.where(lo > hi, (lo + hi) / 2.0, target)


class LineHysteresis:
  def __init__(self):
    self.left = self.right = False

  @staticmethod
  def _step(on: bool, prob) -> bool:
    if prob is None:                 # this side's line is unusable: it no longer counts (the other side is untouched)
      return False
    return True if prob > PROB_ON else False if prob < PROB_OFF else on

  def update(self, l_prob, r_prob) -> tuple[bool, bool]:
    self.left, self.right = self._step(self.left, l_prob), self._step(self.right, r_prob)
    return self.left, self.right

  def reset(self):
    self.left = self.right = False


def curvature_cap(v_ego: float) -> float:
  """The most curvature the laneless correction itself may add at this speed (1/m)."""
  return min(MAX_LAT_ACCEL / max(v_ego, 1.0) ** 2, MAX_CURVATURE)


class ClampSteerCorrector:
  """The laneless half of the clamp: update() returns a curvature (1/m, + right) to add to the model's.

  It follows its target at most SLEW_RATE, so a correction eases in and out while the plan comes and goes. A protective
  stop is immediate instead: a lane change or a turn signal (and LANE_CHANGE_HOLDOFF_S after), the clamp switched off
  or its gain at 0, below 5 km/h, stale or invalid model input (usable=False), the laneless branch not running and bad
  model data all set the output to 0 at once (controlsd's 0.1 s filter and clip_curvature's jerk limit still smooth
  the request). The output never exceeds the current speed's cap, also after the slew limit. alert_active is the ding
  (controlsState.clampSteerActive): on above ALERT_ON, off below ALERT_OFF, then quiet for ALERT_COOLDOWN_S.

  controlsd calls it every control cycle, also when the laneless branch is not steering (usable=False), so the lane
  change hold-off and the alert's cooldown keep their history."""

  def __init__(self):
    self.lines = LineHysteresis()
    self.correcting = False      # past the deadband
    self.correction = 0.0        # the last output
    self.alert_active = False
    self._alert_quiet_until = 0.0
    self._holdoff_until = 0.0
    self._last_t: float | None = None
    self._left_lost = self._right_lost = False   # a line it was using is broken (sticky until it is fine or gone)

  def update(self, md, v_ego: float, lane_changing: bool, settings: ClampSettings, now: float | None = None,
             usable: bool = True) -> float:
    now = time.monotonic() if now is None else now
    target = self._target(md, v_ego, lane_changing, settings, now, usable)
    if target is None:                           # a protective stop: off at once
      self.correcting = False
      self.correction = 0.0
    elif self._last_t is None or now - self._last_t > GAP_RESET_S:
      self.correction = 0.0                      # it was not running: start from zero
    else:
      step = SLEW_RATE * min(max(now - self._last_t, 0.0), 0.1)
      self.correction += float(np.clip(target - self.correction, -step, step))
    cap = curvature_cap(v_ego)
    self.correction = float(np.clip(self.correction, -cap, cap))   # the cap shrinks as the car speeds up
    self._last_t = now
    self._update_alert(settings, now)
    return self.correction

  def _target(self, md, v_ego, lane_changing, settings, now, usable):
    """The correction to ease toward, or None for a protective stop."""
    if lane_changing:                            # crossing the line is the point; hold off a while after it too
      self._holdoff_until = now + LANE_CHANGE_HOLDOFF_S
    if lane_changing or now < self._holdoff_until or not usable or not settings.enabled or settings.gain <= 0.0:
      self.lines.reset()
      return None
    strength = fade_in(v_ego)
    if strength <= 0.0:
      return None
    try:
      return self._compute(md, v_ego, settings, strength)
    except (IndexError, ValueError, TypeError, AttributeError):
      return None

  def _compute(self, md, v_ego, settings, strength):
    lines, edges = md.laneLines, md.roadEdges
    if len(md.position.x) < 2 or len(md.position.y) < 2:
      return None
    ahead = float(np.clip(v_ego * LOOKAHEAD_TIME, LOOKAHEAD_MIN, LOOKAHEAD_MAX))
    path_y = float(np.interp(ahead, md.position.x, md.position.y))
    if not math.isfinite(path_y):
      return None

    def at(line):
      return float(np.interp(ahead, line.x, line.y))

    def line_state(i):
      """'ok', 'missing' (no line in the message) or 'broken' (its arrays do not fit together)."""
      try:
        if len(lines) <= i or len(md.laneLineProbs) <= i:
          return 'missing'
        nx, ny = len(lines[i].x), len(lines[i].y)
      except (TypeError, AttributeError):
        return 'broken'
      if nx < 2 and ny < 2:
        return 'missing'
      return 'ok' if nx >= 2 and ny >= 2 and nx == ny else 'broken'
    # each side on its own: one side's broken line never makes the other side's good line stop counting
    left_state, right_state = line_state(1), line_state(2)
    left_ok, right_ok = left_state == 'ok', right_state == 'ok'
    # a line it was using that breaks is a broken boundary for as long as it stays broken (no quiet switch to the road
    # edge on that side); only the line coming back, or no line at all in the message, ends that
    left_lost = left_state == 'broken' and (self.lines.left or self._left_lost)
    right_lost = right_state == 'broken' and (self.lines.right or self._right_lost)
    self._left_lost, self._right_lost = left_lost, right_lost
    left_on, right_on = self.lines.update(float(md.laneLineProbs[1]) if left_ok else None,
                                          float(md.laneLineProbs[2]) if right_ok else None)
    edges_ok = len(edges) >= 2 and len(md.roadEdgeStds) >= 2

    def side_bound(line_on, line, edge_idx, sign, min_lane, min_edge):
      """One side's bound, on its own: a seen line first, else a sure road edge, else none. Returns (bound or None,
      broken): broken when the boundary it chose could not be worked out (bad numbers), not when there is none."""
      try:
        if line_on:
          b = at(line) - sign * min_lane
        elif edges_ok and len(edges[edge_idx].x) >= 2 and len(edges[edge_idx].y) >= 2 and \
             float(md.roadEdgeStds[edge_idx]) < EDGE_STD_MAX:
          std = float(md.roadEdgeStds[edge_idx])
          b = at(edges[edge_idx]) - sign * std * EDGE_STD_MARGIN - sign * min_edge
        else:
          return None, False
      except (IndexError, ValueError, TypeError, AttributeError):
        return None, True
      return (b, False) if math.isfinite(b) else (None, True)

    lo, lo_broken = (None, True) if left_lost else side_bound(left_on, lines[1] if left_ok else None, 0, -1,
                                                               settings.min_lane, settings.min_edge)
    hi, hi_broken = (None, True) if right_lost else side_bound(right_on, lines[2] if right_ok else None, 1, 1,
                                                                settings.min_lane, settings.min_edge)
    lo_broken = lo_broken or (lo is None and left_state == 'broken')
    hi_broken = hi_broken or (hi is None and right_state == 'broken')
    if lo is None and hi is None:
      self.correcting = False
      # nothing left to measure against: a broken boundary is a protective stop (at once), no boundary seen eases out
      return None if (lo_broken or hi_broken) else 0.0
    target_y = float(clamp_target(path_y, -np.inf if lo is None else lo, np.inf if hi is None else hi))
    violation = abs(target_y - path_y)
    self.correcting = violation >= DEADBAND_OFF if self.correcting else violation > DEADBAND_ON
    if not self.correcting:
      return 0.0
    # pure pursuit: the curvature that reaches target_y at `ahead` metres (+ y right -> + curvature right)
    kappa = 2.0 * (target_y - path_y) * strength / ahead ** 2 * settings.gain
    cap = curvature_cap(v_ego)
    return float(np.clip(kappa, -cap, cap))

  def _update_alert(self, settings, now) -> None:
    if not settings.alert:
      self.alert_active = False
      return
    size = abs(self.correction)
    if self.alert_active:
      if size < ALERT_OFF:
        self.alert_active = False
        self._alert_quiet_until = now + ALERT_COOLDOWN_S
    elif size >= ALERT_ON and now >= self._alert_quiet_until:
      self.alert_active = True
