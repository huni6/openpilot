import math

from selfdrive.carrot.cluster.cluster_layout import ambient_power_gauge, ambient_bsm_edges, extrapolated_media_position_ms, smooth_ambient_accel


def test_ambient_power_gauge_maps_accel_to_signed_fill():
  assert ambient_power_gauge(0.0) == ("idle", 0.0)
  assert ambient_power_gauge(2.5) == ("power", 0.5)
  assert ambient_power_gauge(5.0) == ("power", 1.0)
  assert ambient_power_gauge(-10.0) == ("brake", 1.0)


def test_smooth_ambient_accel_filters_noise_and_uses_deadband():
  assert smooth_ambient_accel(0.0, 0.05, 0.1) == 0.0
  first = smooth_ambient_accel(0.0, 2.0, 0.1)
  second = smooth_ambient_accel(first, 2.0, 0.1)
  assert 0.0 < first < second < 2.0
  assert math.isfinite(smooth_ambient_accel(float("nan"), None, 0.1))


def test_ambient_bsm_edges_keeps_left_and_right_independent():
  assert ambient_bsm_edges(False, False) == (False, False)
  assert ambient_bsm_edges(True, False) == (True, False)
  assert ambient_bsm_edges(False, True) == (False, True)


def test_extrapolated_media_position_advances_only_while_playing():
  assert extrapolated_media_position_ms(10_000, 60_000, True, 100_000, 102_500) == 12_500
  assert extrapolated_media_position_ms(10_000, 60_000, False, 100_000, 102_500) == 10_000
  assert extrapolated_media_position_ms(59_000, 60_000, True, 100_000, 102_500) == 60_000
  assert extrapolated_media_position_ms(None, 60_000, True, 100_000, 102_500) is None
