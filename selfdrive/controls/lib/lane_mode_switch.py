"""Two lane mode switches from Carrot Web (user request 2026-11-19).

- LaneModeWithLead (toggle): lane mode while a lead car is ahead (radarState.leadOne, as the onroad screen shows it),
  on top of the speed rule (UseLaneLineSpeed). The lateral planner ORs it in; the model-speed guard (laneless_only)
  still wins, as it does for the speed rule.
- LanelessCurvature (x0.0001 1/m, 0 off): steer laneless in a curve at least this sharp, even while lane mode is on.
  controlsd switches its curvature source, as it already does for UseLaneLineCurveSpeed, so the onroad lane mode mark
  (controlsState.activeLaneLine) follows it. It looks at the model's curvature now and over the next second, turns
  laneless at the threshold and back only after a second below 80% of it, so an S-bend or a wobbling plan does not flip
  the source back and forth.

Both only choose between the existing lane mode and laneless steering; neither adds steering of its own.
"""
from __future__ import annotations

import math

import numpy as np

LEAD_ON_S = 0.5    # a lead seen this long turns lead lane mode on ...
LEAD_OFF_S = 2.0   # ... and gone this long turns it off (radar and vision leads drop out for a frame or two)

CURVATURE_SETTING_RANGE = (1, 200)    # x0.0001 1/m: up to 0.02 (radius 50 m); 0 is off
CURVATURE_OFF_RATIO = 0.8             # back to lane mode below 80% of the threshold ...
CURVATURE_OFF_HOLD_S = 1.0            # ... held for a second
CURVATURE_LOOKAHEAD_S = 1.0           # the model plan's curvature this far ahead counts too
MIN_PLAN_SPEED = 1.0                  # m/s, under which the plan's yaw rate says nothing about curvature


def lead_lane_mode_setting(value) -> bool:
  return value is True or (isinstance(value, int) and not isinstance(value, bool) and value == 1)


def laneless_curvature_setting(value) -> float:
  """The threshold in 1/m, or 0.0 (off) for 0, a missing or a corrupt value."""
  if isinstance(value, bool) or not isinstance(value, int):
    return 0.0
  if not CURVATURE_SETTING_RANGE[0] <= value <= CURVATURE_SETTING_RANGE[1]:
    return 0.0
  return value * 1e-4


def read_lane_mode_settings(params) -> tuple[bool, float]:
  """(LaneModeWithLead, LanelessCurvature in 1/m); a missing key (an old native registry) or a bad value is off."""
  def get(key):
    try:
      return params.get(key, return_default=True)
    except Exception:
      return None
  return lead_lane_mode_setting(get("LaneModeWithLead")), laneless_curvature_setting(get("LanelessCurvature"))


class LeadLaneMode:
  """Debounced 'a lead car is ahead' for lane mode: on after LEAD_ON_S of a lead, off after LEAD_OFF_S without one."""

  def __init__(self):
    self.active = False
    self._since: float | None = None   # when the lead (or its absence) that would change `active` started

  def update(self, enabled: bool, lead: bool, now: float) -> bool:
    if not enabled:
      self.active = False
      self._since = None
      return False
    if lead == self.active:
      self._since = None
    else:
      if self._since is None:
        self._since = now
      if now - self._since >= (LEAD_ON_S if lead else LEAD_OFF_S):
        self.active = lead
        self._since = None
    return self.active


def model_curvature(md) -> float:
  """The sharpest curvature (1/m, absolute) the model asks for now or plans over the next CURVATURE_LOOKAHEAD_S;
  0.0 when there is nothing usable."""
  values = []
  try:
    desired = float(md.action.desiredCurvature)
    if math.isfinite(desired):
      values.append(abs(desired))
  except (AttributeError, TypeError, ValueError):
    pass
  try:
    t, yaw_rate, speed = md.orientationRate.t, md.orientationRate.z, md.velocity.x
    n = min(len(t), len(yaw_rate), len(speed))
    for i in range(n):
      if float(t[i]) > CURVATURE_LOOKAHEAD_S:
        break
      v = float(speed[i])   # the planned speed there: a car slowing for the bend turns as sharply at a lower speed
      if not v >= MIN_PLAN_SPEED:
        continue
      k = abs(float(yaw_rate[i])) / v
      if math.isfinite(k):
        values.append(k)
  except (AttributeError, TypeError, ValueError):
    pass
  return float(np.max(values)) if values else 0.0


class CurveLaneless:
  """Laneless in curves: on at the threshold, off after CURVATURE_OFF_HOLD_S under CURVATURE_OFF_RATIO of it."""

  def __init__(self):
    self.active = False
    self.curvature = 0.0
    self._below_since: float | None = None

  def update(self, threshold: float, md, now: float) -> bool:
    if threshold <= 0.0:
      self.active = False
      self._below_since = None
      self.curvature = 0.0
      return False
    self.curvature = model_curvature(md)
    if self.curvature >= threshold:
      self.active = True
      self._below_since = None
    elif self.active:
      if self.curvature >= threshold * CURVATURE_OFF_RATIO:
        self._below_since = None
      else:
        if self._below_since is None:
          self._below_since = now
        if now - self._below_since >= CURVATURE_OFF_HOLD_S:
          self.active = False
          self._below_since = None
    return self.active
