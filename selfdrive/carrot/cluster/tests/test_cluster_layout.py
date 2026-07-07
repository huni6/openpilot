from selfdrive.carrot.cluster.cluster_layout import ambient_power_gauge, ambient_bsm_edges


def test_ambient_power_gauge_maps_accel_to_signed_fill():
  assert ambient_power_gauge(0.0) == ("idle", 0.0)
  assert ambient_power_gauge(1.25) == ("power", 0.5)
  assert ambient_power_gauge(2.5) == ("power", 1.0)
  assert ambient_power_gauge(-10.0) == ("brake", 1.0)


def test_ambient_bsm_edges_keeps_left_and_right_independent():
  assert ambient_bsm_edges(False, False) == (False, False)
  assert ambient_bsm_edges(True, False) == (True, False)
  assert ambient_bsm_edges(False, True) == (False, True)
