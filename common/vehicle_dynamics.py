"""Live centre-of-gravity setting (VehicleCenterToFrontRatio), from leehyuk1108/carrotpilot WIP-FR's live vehicle
specifications (common/vehicle_dynamics.py, docs/live_vehicle_specs_20261020.md). Only the ratio is ported: the mass
stays the installed vehicle's.

The setting is the front axle to centre of gravity distance divided by the wheelbase (0.10-0.90), not a load share or
a height. Stored 0 (or nothing, or a value that is not a valid ratio) follows the installed vehicle (CarParams), so a
fresh install and the web's default button keep the vehicle's own baseline even if a later version changes it.

paramsd is the one reader: twice a second, moving the applied ratio toward the setting at 0.02 per second, with the
tyre stiffnesses rescaled for the new axle loads as WIP-FR does. It updates its Kalman globals and publishes the applied
snapshot in liveParameters.vehicleDynamics; controlsd (VehicleModel) and the lateral planner use that snapshot and never
poll the setting themselves.
"""
from dataclasses import asdict, dataclass
import math

RATIO_KEY = "VehicleCenterToFrontRatio"
RATIO_RANGE = (0.1, 0.9)
READ_INTERVAL_S = 0.5
CENTER_RATIO_RATE_S = 0.02   # ratio per second: 0.40 to 0.50 takes 5 s


def validate_center_ratio(value) -> float:
  """A ratio the web may store: a finite decimal 0.10-0.90 (not a bool, not a percentage)."""
  try:
    number = float(value)
  except (TypeError, ValueError, OverflowError):
    number = math.nan
  if isinstance(value, bool) or not math.isfinite(number) or not RATIO_RANGE[0] <= number <= RATIO_RANGE[1]:
    raise ValueError(f"{RATIO_KEY}: use a decimal from {RATIO_RANGE[0]} to {RATIO_RANGE[1]}, such as 0.50")
  return number


def stored_center_ratio(raw) -> float | None:
  """The custom ratio a stored value asks for, or None to follow the installed vehicle (0, missing or invalid)."""
  if raw is None or raw in ("", b""):
    return None
  try:
    return validate_center_ratio(raw.decode() if isinstance(raw, bytes) else raw)
  except (ValueError, UnicodeDecodeError):
    return None


def center_ratio_default(cp) -> float:
  """The installed vehicle's ratio (CarParams uses float32: drop representation noise, not useful decimals)."""
  if cp.notCar or not math.isfinite(cp.wheelbase) or cp.wheelbase <= 0:
    raise ValueError("vehicle specifications are unavailable")
  return validate_center_ratio(round(cp.centerToFront / cp.wheelbase, 6))


def center_ratio_to_store(value, default: float | None) -> float:
  """What the web's choice is stored as: 0.0 (follow the vehicle) when it is the vehicle's own ratio."""
  ratio = validate_center_ratio(value)
  if default is not None and abs(ratio - default) < 5e-7:
    return 0.0
  return ratio


@dataclass(frozen=True)
class VehicleDynamics:
  mass: float
  wheelbase: float
  centerToFront: float
  rotationalInertia: float
  tireStiffnessFront: float
  tireStiffnessRear: float

  @classmethod
  def from_car_params(cls, cp):
    return cls(**{key: float(getattr(cp, key)) for key in cls.__dataclass_fields__})

  def scaled(self, mass, center_to_front):
    if mass == self.mass and center_to_front == self.centerToFront:
      return self
    mass_scale = mass / self.mass
    return VehicleDynamics(
      mass, self.wheelbase, center_to_front,
      self.rotationalInertia * mass_scale,
      self.tireStiffnessFront * mass_scale * (self.wheelbase - center_to_front) / (self.wheelbase - self.centerToFront),
      self.tireStiffnessRear * mass_scale * center_to_front / self.centerToFront,
    )

  def to_message(self):
    return {"valid": True, **asdict(self)}

  def kalman_globals(self):
    return dict(mass=self.mass, rotational_inertia=self.rotationalInertia,
                center_to_front=self.centerToFront, center_to_rear=self.wheelbase - self.centerToFront,
                stiffness_front=self.tireStiffnessFront, stiffness_rear=self.tireStiffnessRear)


class LiveVehicleDynamics:
  """paramsd's reader: the one place the setting is polled; consumers get the published snapshot."""
  def __init__(self, cp, params):
    self.base = VehicleDynamics.from_car_params(cp)
    self.current = self.base
    self.params = params
    self.enabled = not cp.notCar and 0 < cp.centerToFront < cp.wheelbase
    self.target_front = self.base.centerToFront
    self.last_read = None
    self.last_update = None

  def update(self, now):
    if not self.enabled or not math.isfinite(now):
      return self.current
    if self.last_read is None or now < self.last_read or now - self.last_read >= READ_INTERVAL_S:
      try:
        raw = self.params.get(RATIO_KEY)
      except Exception:
        # A source update can precede rebuilding the native Params registry: an unreadable key must not stop the
        # parameter learner, and it keeps the last target.
        raw = False
      if raw is not False:
        ratio = stored_center_ratio(raw)
        self.target_front = self.base.centerToFront if ratio is None else self.base.wheelbase * ratio
      self.last_read = now

    # Bound both tuning steps and scheduler gaps; never replace the controller or reset its state when it is edited.
    dt = 0.0 if self.last_update is None else max(0.0, min(now - self.last_update, 0.1))
    self.last_update = now
    front = move_towards(self.current.centerToFront, self.target_front, self.base.wheelbase * CENTER_RATIO_RATE_S * dt)
    self.current = self.base.scaled(self.base.mass, front)
    return self.current


def move_towards(current, target, limit):
  return current + max(-limit, min(target - current, limit))


CONSUMER_RATE_MARGIN = 1.5   # a consumer may move up to 1.5x paramsd's rate, so it keeps up with a normal ramp


class DynamicsFollower:
  """A consumer's applied dynamics (controlsd's VehicleModel, the lateral planner's MPC factors): it moves toward the
  latest valid snapshot at most CENTER_RATIO_RATE_S x CONSUMER_RATE_MARGIN, so a snapshot that arrives after a gap
  (liveParameters invalid or late while the ratio kept ramping in paramsd) is not applied in one step. None from
  update() means nothing changed."""
  def __init__(self, cp):
    self.base = VehicleDynamics.from_car_params(cp)
    self.current = self.base
    self.last_time = None

  def update(self, snapshot, now):
    last, self.last_time = self.last_time, now
    if snapshot is None or not math.isfinite(now):
      return None
    dt = 0.0 if last is None else max(0.0, min(now - last, 0.1))
    limit = self.base.wheelbase * CENTER_RATIO_RATE_S * CONSUMER_RATE_MARGIN * dt
    front = move_towards(self.current.centerToFront, snapshot.centerToFront, limit)
    if front == self.current.centerToFront:
      return None
    self.current = self.base.scaled(self.base.mass, front)
    return self.current


def read_live_vehicle_dynamics(sm, cp):
  """The applied snapshot from liveParameters; None (keep the last model) for old recordings, mixed versions or an
  invalid message."""
  if not sm.all_checks(["liveParameters"]):
    return None
  lp = sm["liveParameters"]
  if not lp.valid:
    return None
  msg = getattr(lp, "vehicleDynamics", None)
  if msg is None or not msg.valid:
    return None
  values = VehicleDynamics.from_car_params(msg)
  if (not all(math.isfinite(v) and v > 0 for v in asdict(values).values())
      or not math.isclose(values.wheelbase, cp.wheelbase, rel_tol=1e-6)
      or not math.isclose(values.mass, cp.mass, rel_tol=1e-6)
      or not 0 < values.centerToFront < values.wheelbase):
    return None
  return values
