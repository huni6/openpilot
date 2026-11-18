"""VehicleCenterToFrontRatio (common/vehicle_dynamics.py): WIP-FR's live vehicle specification tests, ratio only."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from opendbc.car import STD_CARGO_KG, scale_rot_inertia, scale_tire_stiffness
from opendbc.car.vehicle_model import VehicleModel
from cereal import car, log
from openpilot.common.vehicle_dynamics import (
  CENTER_RATIO_RATE_S, RATIO_KEY, LiveVehicleDynamics, VehicleDynamics, center_ratio_default, center_ratio_to_store,
  read_live_vehicle_dynamics, stored_center_ratio, validate_center_ratio,
)


def car_params(mass=1955, ratio=0.4):
  mass += STD_CARGO_KG
  front = 2.9 * ratio
  stiffness = scale_tire_stiffness(mass, 2.9, front, 0.65)
  return car.CarParams.new_message(
    mass=mass, wheelbase=2.9, centerToFront=front, steerRatio=16.0,
    rotationalInertia=scale_rot_inertia(mass, 2.9),
    tireStiffnessFront=stiffness[0], tireStiffnessRear=stiffness[1],
  )


def messages(cp, dynamics=None):
  lp = log.LiveParametersData.new_message(valid=True)
  if dynamics is not None:
    lp.vehicleDynamics = dynamics.to_message()

  class Messages(dict):
    checks = True

    def all_checks(self, services):
      assert services == ["liveParameters"]
      return self.checks

  return Messages(liveParameters=lp)


@pytest.mark.parametrize("value", ["0.70", 0.1, 0.9, 0.735, "0.5"])
def test_valid_ratios(value):
  assert validate_center_ratio(value) == pytest.approx(float(value))


@pytest.mark.parametrize("value", [0, 0.09, 0.91, 70, "70", "nan", "inf", None, True, "x", ""])
def test_invalid_ratios(value):
  with pytest.raises(ValueError):
    validate_center_ratio(value)


@pytest.mark.parametrize("raw, expected", [(None, None), ("", None), (b"", None), (0.0, None), ("0", None), (b"0", None),
                                           (0.5, 0.5), ("0.55", 0.55), (b"0.6", 0.6), (1.5, None), (float("nan"), None)])
def test_stored_value_zero_or_invalid_follows_the_vehicle(raw, expected):
  assert stored_center_ratio(raw) == (None if expected is None else pytest.approx(expected))


def test_vehicle_default_and_storing_it_as_zero():
  cp = car_params(ratio=0.5)
  default = center_ratio_default(cp)
  assert default == pytest.approx(0.5)
  assert center_ratio_to_store(default, default) == 0.0     # the vehicle's own ratio follows the vehicle
  assert center_ratio_to_store("0.50", default) == 0.0
  assert center_ratio_to_store(0.51, default) == pytest.approx(0.51)
  assert center_ratio_to_store(0.5, None) == pytest.approx(0.5)   # vehicle unknown: kept as asked
  with pytest.raises(ValueError):
    center_ratio_to_store(0, default)
  cp.notCar = True
  with pytest.raises(ValueError):
    center_ratio_default(cp)


@pytest.mark.parametrize("ratio", [0.4, 0.5, 0.45])
def test_follow_the_vehicle_preserves_installed_car_params_exactly(ratio):
  cp = car_params(ratio=ratio)
  before = cp.to_dict()
  for stored in (b"0", None, 0.0, 2.0):
    tuning = LiveVehicleDynamics(cp, NS(get=lambda name, stored=stored: stored))
    for step in range(100):
      assert tuning.update(step * 0.05) == VehicleDynamics.from_car_params(cp)
  assert cp.to_dict() == before


def test_live_update_ramps_recomputes_and_restores_without_restarting():
  cp = car_params()
  stored = {}
  tuning = LiveVehicleDynamics(cp, NS(get=stored.get))
  base = tuning.update(0)
  stored[RATIO_KEY] = 0.5
  previous = base
  for step in range(1, 401):
    current = tuning.update(step * 0.05)
    assert abs(current.centerToFront - previous.centerToFront) <= cp.wheelbase * CENTER_RATIO_RATE_S * 0.05 + 1e-8
    assert current.mass == cp.mass and current.rotationalInertia == pytest.approx(cp.rotationalInertia)
    previous = current
  assert current.centerToFront == pytest.approx(2.9 * 0.5)
  front, rear = scale_tire_stiffness(cp.mass, cp.wheelbase, current.centerToFront, 0.65)
  assert current.tireStiffnessFront == pytest.approx(front)
  assert current.tireStiffnessRear == pytest.approx(rear)
  stored[RATIO_KEY] = 0.0      # the web's default button
  for step in range(401, 801):
    current = tuning.update(step * 0.05)
  assert current == base
  assert VehicleDynamics.from_car_params(cp) == base


def test_read_cadence_and_no_jumps_after_pause_or_backwards_clock():
  cp = car_params()
  reads = []
  tuning = LiveVehicleDynamics(cp, NS(get=lambda key: (reads.append(key), 0.0)[1]))
  tuning.update(0)
  tuning.update(0.49)
  assert len(reads) == 1
  tuning.update(0.5)
  assert len(reads) == 2
  tuning.params = NS(get=lambda key: 0.9)
  current = tuning.update(1000)
  assert current.centerToFront - cp.centerToFront == pytest.approx(cp.wheelbase * 0.002)   # one 0.1 s step at most
  assert tuning.update(999) == current
  assert tuning.update(float("nan")) == current


def test_not_car_never_reads_the_setting():
  cp = car_params()
  cp.notCar = True
  tuning = LiveVehicleDynamics(cp, NS(get=lambda key: pytest.fail("notCar tuning read")))
  assert tuning.update(10) == tuning.base


def test_old_native_key_registry_does_not_crash_the_learner_and_keeps_the_target():
  class UnknownKeyName(Exception):
    pass

  stored = {RATIO_KEY: 0.5}
  tuning = LiveVehicleDynamics(car_params(), NS(get=stored.get))
  tuning.update(0)

  def get(key):
    raise UnknownKeyName(key)
  tuning.params = NS(get=get)
  tuning.update(1)
  assert tuning.target_front == pytest.approx(2.9 * 0.5)


def test_real_capnp_message_roundtrip_and_vehicle_model_learned_values_preserved():
  cp = car_params()
  dynamics = VehicleDynamics.from_car_params(cp).scaled(cp.mass, 2.9 * 0.5)
  sm = messages(cp, dynamics)
  with log.LiveParametersData.from_bytes(sm["liveParameters"].to_bytes()) as lp:
    sm["liveParameters"] = lp
    assert read_live_vehicle_dynamics(sm, cp) == dynamics
  vm = VehicleModel(cp)
  vm.update_params(0.85, 18.2)
  vm.update_dynamics(dynamics)
  assert vm.m == dynamics.mass
  assert vm.aF == dynamics.centerToFront
  assert vm.aR == pytest.approx(cp.wheelbase - dynamics.centerToFront)
  assert vm.cF == pytest.approx(dynamics.tireStiffnessFront * 0.85)
  assert vm.cR == pytest.approx(dynamics.tireStiffnessRear * 0.85)
  assert vm.j == dynamics.rotationalInertia
  assert vm.sR == 18.2


@pytest.mark.parametrize("field,value", [
  ("mass", float("nan")), ("mass", 3000.0), ("rotationalInertia", 0), ("tireStiffnessRear", -1),
  ("tireStiffnessFront", float("inf")), ("centerToFront", 4), ("wheelbase", 2.5),
])
def test_invalid_live_snapshot_rejected(field, value):
  cp = car_params()
  sm = messages(cp, replace(VehicleDynamics.from_car_params(cp), **{field: value}))
  assert read_live_vehicle_dynamics(sm, cp) is None


def test_missing_stale_or_invalid_live_snapshot_leaves_consumers_unchanged():
  cp = car_params()
  sm = messages(cp)
  assert read_live_vehicle_dynamics(sm, cp) is None  # old schema/log
  sm["liveParameters"].vehicleDynamics = VehicleDynamics.from_car_params(cp).to_message()
  sm.checks = False
  assert read_live_vehicle_dynamics(sm, cp) is None
  sm.checks = True
  sm["liveParameters"].valid = False
  assert read_live_vehicle_dynamics(sm, cp) is None


def test_kalman_globals_include_all_dependent_constants():
  values = VehicleDynamics.from_car_params(car_params())
  assert values.kalman_globals() == dict(
    mass=values.mass, rotational_inertia=values.rotationalInertia,
    center_to_front=values.centerToFront, center_to_rear=values.wheelbase-values.centerToFront,
    stiffness_front=values.tireStiffnessFront, stiffness_rear=values.tireStiffnessRear,
  )
