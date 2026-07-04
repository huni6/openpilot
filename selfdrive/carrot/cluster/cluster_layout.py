from __future__ import annotations

import math

try:
  from cluster_config import MAX_ACCEL_MPS2
except ModuleNotFoundError:
  from selfdrive.carrot.cluster.cluster_config import MAX_ACCEL_MPS2


def _clamp(value: float, low: float, high: float) -> float:
  return max(low, min(high, value))


def ambient_power_gauge(accel_mps2: float | int | None) -> tuple[str, float]:
  try:
    value = 0.0 if accel_mps2 is None else float(accel_mps2)
  except (TypeError, ValueError):
    value = 0.0
  if not math.isfinite(value) or abs(value) < 0.005:
    return "idle", 0.0
  return ("power" if value > 0.0 else "brake"), _clamp(abs(value) / MAX_ACCEL_MPS2, 0.0, 1.0)


def ambient_bsm_edges(left_blindspot: bool, right_blindspot: bool) -> tuple[bool, bool]:
  return bool(left_blindspot), bool(right_blindspot)
