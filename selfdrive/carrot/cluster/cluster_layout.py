from __future__ import annotations

import math

try:
  from cluster_config import AMBIENT_ACCEL_DEADBAND_MPS2, AMBIENT_ACCEL_SMOOTH_TAU_SECONDS, AMBIENT_MAX_ACCEL_MPS2
except ModuleNotFoundError:
  from selfdrive.carrot.cluster.cluster_config import AMBIENT_ACCEL_DEADBAND_MPS2, AMBIENT_ACCEL_SMOOTH_TAU_SECONDS, AMBIENT_MAX_ACCEL_MPS2


def _clamp(value: float, low: float, high: float) -> float:
  return max(low, min(high, value))


def ambient_power_gauge(accel_mps2: float | int | None) -> tuple[str, float]:
  try:
    value = 0.0 if accel_mps2 is None else float(accel_mps2)
  except (TypeError, ValueError):
    value = 0.0
  if not math.isfinite(value) or abs(value) < 0.005:
    return "idle", 0.0
  return ("power" if value > 0.0 else "brake"), _clamp(abs(value) / AMBIENT_MAX_ACCEL_MPS2, 0.0, 1.0)


def smooth_ambient_accel(previous: float, accel_mps2: float | int | None, elapsed_seconds: float) -> float:
  try:
    target = 0.0 if accel_mps2 is None else float(accel_mps2)
  except (TypeError, ValueError):
    target = 0.0
  if not math.isfinite(target) or abs(target) < AMBIENT_ACCEL_DEADBAND_MPS2:
    target = 0.0
  if not math.isfinite(previous):
    previous = 0.0
  elapsed = _clamp(float(elapsed_seconds), 0.0, 0.25)
  alpha = 1.0 - math.exp(-elapsed / AMBIENT_ACCEL_SMOOTH_TAU_SECONDS)
  value = previous + (target - previous) * alpha
  return 0.0 if target == 0.0 and abs(value) < 0.01 else value


def extrapolated_media_position_ms(
  position_ms: int | None,
  duration_ms: int | None,
  is_playing: bool,
  received_at_ms: int | None,
  now_ms: int,
) -> int | None:
  if position_ms is None or position_ms < 0:
    return None
  position = int(position_ms)
  if is_playing and received_at_ms is not None and now_ms > received_at_ms:
    position += now_ms - received_at_ms
  if duration_ms is not None and duration_ms >= 0:
    position = min(position, int(duration_ms))
  return max(0, position)


def ambient_bsm_edges(left_blindspot: bool, right_blindspot: bool) -> tuple[bool, bool]:
  return bool(left_blindspot), bool(right_blindspot)
