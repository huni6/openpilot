#!/usr/bin/env python3
import math
import time
from numbers import Number

from cereal import car, log
import cereal.messaging as messaging
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import config_realtime_process, Priority, Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.common.vehicle_dynamics import DynamicsFollower, read_live_vehicle_dynamics
import numpy as np
from collections import deque

from opendbc.car.car_helpers import interfaces
from opendbc.car.vehicle_model import VehicleModel

from openpilot.selfdrive.controls.lib.drive_helpers import clip_curvature, get_lag_adjusted_curvature, lateral_accel_limits, \
                                                       lateral_jerk_limit, MAX_LATERAL_ACCEL_NO_ROLL, \
                                                       MAX_LATERAL_ACCEL_NO_ROLL_LOW_SPEED, MAX_LATERAL_JERK
from openpilot.selfdrive.controls.lib.min_distance_clamp import ClampSettings, ClampSteerCorrector, read_clamp_settings
from openpilot.selfdrive.controls.lib.lane_mode_switch import CurveLaneless, read_lane_mode_settings
from openpilot.selfdrive.controls.lib.latcontrol import LatControl, MIN_LATERAL_CONTROL_SPEED
from openpilot.selfdrive.controls.lib.latcontrol_pid import LatControlPID
from openpilot.selfdrive.controls.lib.latcontrol_angle import LatControlAngle, STEER_ANGLE_SATURATION_THRESHOLD
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.controls.lib.longcontrol import LongControl


from openpilot.common.realtime import DT_CTRL, DT_MDL
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N
from selfdrive.modeld.modeld import LAT_SMOOTH_SECONDS
from openpilot.selfdrive.locationd.helpers import PoseCalibrator, Pose

from openpilot.selfdrive.carrot.carrot_controls import CarrotControls

State = log.SelfdriveState.OpenpilotState
LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection
Desire = log.Desire
LAT_ACCEL_LIMIT_RATE = 0.5  # m/s^2 per second: lowering LatAccelMaxLow from 4.5 to 3.0 takes 3 s, also in a curve

ACTUATOR_FIELDS = tuple(car.CarControl.Actuators.schema.fields.keys())


class Controls:
  def __init__(self) -> None:
    self.params = Params()
    cloudlog.info("controlsd is waiting for CarParams")
    self.CP = messaging.log_from_bytes(self.params.get("CarParams", block=True), car.CarParams)
    cloudlog.info("controlsd got CarParams")

    self.CI = interfaces[self.CP.carFingerprint](self.CP)

    self.disable_dm = False

    self.sm = messaging.SubMaster(['liveDelay', 'liveParameters', 'liveTorqueParameters', 'modelV2', 'selfdriveState',
                                   'liveCalibration', 'livePose', 'longitudinalPlan', 'carState', 'carOutput',
                                   'carrotMan', 'lateralPlan', 'radarState',
                                   'driverMonitoringState', 'onroadEvents', 'driverAssistance'], poll='selfdriveState')
    self.pm = messaging.PubMaster(['carControl', 'controlsState'])

    self.steer_limited_by_safety = False
    self.curvature = 0.0
    self.desired_curvature = 0.0

    self.pose_calibrator = PoseCalibrator()
    self.calibrated_pose: Pose | None = None
    
    self.side_state = {
        "left":  {"main": {"dRel": None, "lat": None}, "sub": {"dRel": None, "lat": None}},
        "right": {"main": {"dRel": None, "lat": None}, "sub": {"dRel": None, "lat": None}},
    }

    self.LoC = LongControl(self.CP)
    self.VM = VehicleModel(self.CP)
    self.LaC: LatControl
    if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
      self.LaC = LatControlAngle(self.CP, self.CI)
    elif self.CP.lateralTuning.which() == 'pid':
      self.LaC = LatControlPID(self.CP, self.CI)
    elif self.CP.lateralTuning.which() == 'torque':
      self.LaC = LatControlTorque(self.CP, self.CI)
    self.carrot_controls = CarrotControls(self.CP)
    # ej lateral limits (LatAccelMaxLow / LatAccelMaxHigh / LatJerkMax), the minimum-distance clamp's laneless half
    # (min_distance_clamp.py) and laneless in sharp curves (lane_mode_switch.py): settings read at 1 Hz in update()
    self.lat_accel_low_target, self.lat_accel_high_target = self.read_lat_accel_limits()
    self.lat_accel_low, self.lat_accel_high = self.lat_accel_low_target, self.lat_accel_high_target
    self.lat_jerk_limit = self.read_lat_jerk_limit()
    self.clamp_steer = ClampSteerCorrector()
    self.clamp_steer_ran = False
    self.clamp_settings = self.read_clamp_settings()
    self.curve_laneless = CurveLaneless()
    self.laneless_curvature = self.read_laneless_curvature()
    self.lanefull_mode_enabled = False
    self.dynamics_follower = DynamicsFollower(self.CP)   # VehicleCenterToFrontRatio, from paramsd's snapshot

  def read_lat_accel_limits(self) -> tuple[float, float]:
    # Params.get casts in Python (a corrupt value becomes None); get_int's native std::stoi would abort on one
    try:
      return lateral_accel_limits(self.params.get("LatAccelMaxLow", return_default=True),
                                  self.params.get("LatAccelMaxHigh", return_default=True))
    except Exception:  # e.g. the native params registry is not rebuilt yet: keep the stock limits
      return MAX_LATERAL_ACCEL_NO_ROLL_LOW_SPEED, MAX_LATERAL_ACCEL_NO_ROLL

  def read_lat_jerk_limit(self) -> float:
    try:
      return lateral_jerk_limit(self.params.get("LatJerkMax", return_default=True))
    except Exception:
      return MAX_LATERAL_JERK

  def read_clamp_settings(self):
    try:
      return read_clamp_settings(self.params)
    except Exception:
      return ClampSettings()

  def read_laneless_curvature(self) -> float:
    try:
      return read_lane_mode_settings(self.params)[1]
    except Exception:
      return 0.0

  def update(self):
    self.sm.update(15)
    if self.sm.frame % 100 == 0:  # 1 Hz: these settings can change while driving (never read per 10 ms cycle)
      self.lat_accel_low_target, self.lat_accel_high_target = self.read_lat_accel_limits()
      self.lat_jerk_limit = self.read_lat_jerk_limit()
      self.clamp_settings = self.read_clamp_settings()
      self.laneless_curvature = self.read_laneless_curvature()
    # a changed limit moves gradually, so lowering it in a curve does not cut the request at once
    step = LAT_ACCEL_LIMIT_RATE * DT_CTRL
    self.lat_accel_low += float(np.clip(self.lat_accel_low_target - self.lat_accel_low, -step, step))
    self.lat_accel_high += float(np.clip(self.lat_accel_high_target - self.lat_accel_high, -step, step))
    if self.sm.updated["liveCalibration"]:
      self.pose_calibrator.feed_live_calib(self.sm['liveCalibration'])
    if self.sm.updated["livePose"]:
      device_pose = Pose.from_live_pose(self.sm['livePose'])
      self.calibrated_pose = self.pose_calibrator.build_calibrated_pose(device_pose)

  def state_control(self):
    CS = self.sm['carState']

    # Update VehicleModel (VehicleCenterToFrontRatio arrives as paramsd's applied snapshot; none keeps the last one)
    dynamics = self.dynamics_follower.update(read_live_vehicle_dynamics(self.sm, self.CP), time.monotonic())
    if dynamics is not None:
      self.VM.update_dynamics(dynamics)
    lp = self.sm['liveParameters']
    x = max(lp.stiffnessFactor, 0.1)
    sr = max(lp.steerRatio, 0.1) * self.params.get_float("SteerRatioRate") / 100.0
    custom_sr = self.params.get_float("CustomSR") / 10.0
    sr = max(custom_sr if custom_sr > 1.0 else sr, 0.1)
    self.VM.update_params(x, sr)

    steer_angle_without_offset = math.radians(CS.steeringAngleDeg - lp.angleOffsetDeg)
    self.curvature = -self.VM.calc_curvature(steer_angle_without_offset, CS.vEgo, lp.roll)

    # Update Torque Params
    if self.CP.lateralTuning.which() == 'torque':
      torque_params = self.sm['liveTorqueParameters']
      if self.sm.all_checks(['liveTorqueParameters']) and torque_params.useParams:
        self.LaC.update_live_torque_params(torque_params.latAccelFactorFiltered, torque_params.latAccelOffsetFiltered,
                                           torque_params.frictionCoefficientFiltered)

    long_plan = self.sm['longitudinalPlan']
    model_v2 = self.sm['modelV2']

    CC = car.CarControl.new_message()
    CC.enabled = self.sm['selfdriveState'].enabled

    # carrot
    gear = car.CarState.GearShifter
    driving_gear = CS.gearShifter not in (gear.neutral, gear.park, gear.reverse, gear.unknown)
    lateral_enabled = driving_gear and self.params.get_bool("AlwaysLateral")
    #self.soft_hold_active = CS.softHoldActive #car.OnroadEvent.EventName.softHold in [e.name for e in self.sm['onroadEvents']]

    # Check which actuators can be enabled
    standstill = abs(CS.vEgo) <= max(self.CP.minSteerSpeed, MIN_LATERAL_CONTROL_SPEED) or CS.standstill
    CC.latActive = ((self.sm['selfdriveState'].active or lateral_enabled) and CS.latEnabled and
                    not CS.steerFaultTemporary and not CS.steerFaultPermanent and not standstill)
    CC.latActive = self.carrot_controls.lat_suspend_control(CS, CC.latActive)
    CC.longActive = CC.enabled and not any(e.overrideLongitudinal for e in self.sm['onroadEvents']) and self.CP.openpilotLongitudinalControl

    actuators = CC.actuators
    actuators.longControlState = self.LoC.long_control_state

    # Enable blinkers while lane changing
    if model_v2.meta.laneChangeState != LaneChangeState.off:
      CC.leftBlinker = model_v2.meta.laneChangeDirection == LaneChangeDirection.left
      CC.rightBlinker = model_v2.meta.laneChangeDirection == LaneChangeDirection.right

    if not CC.latActive:
      self.LaC.reset()
    if not CC.longActive:
      self.LoC.reset()

    # accel PID loop
    pid_accel_limits = self.CI.get_pid_accel_limits(self.CP, CS.vEgo, CS.vCruise * CV.KPH_TO_MS)
    t_since_plan = (self.sm.frame - self.sm.recv_frame['longitudinalPlan']) * DT_CTRL
    accel, aTarget, jerk = self.LoC.update(CC.longActive, CS, long_plan, pid_accel_limits, t_since_plan, self.sm['radarState'])
    actuators.accel = float(accel)
    actuators.aTarget = float(aTarget)
    actuators.jerk = float(jerk)

    # Steering PID loop and lateral MPC
    lat_plan = self.sm['lateralPlan']
    curve_speed_abs = abs(self.sm['carrotMan'].vTurnSpeed)
    # LanelessCurvature: laneless in a curve at least this sharp (lane_mode_switch.py), like UseLaneLineCurveSpeed
    curve_laneless = self.curve_laneless.update(self.laneless_curvature, model_v2, time.monotonic())
    self.lanefull_mode_enabled = (lat_plan.useLaneLines and curve_speed_abs > self.params.get_int("UseLaneLineCurveSpeed")
                                  and not curve_laneless)
    lat_smooth_seconds = self.params.get_float("LatSmoothSec") * 0.01
    steer_actuator_delay = self.params.get_float("SteerActuatorDelay") * 0.01
    if steer_actuator_delay == 0.0:
      steer_actuator_delay = self.sm['liveDelay'].lateralDelay 
    
    def smooth_value(val, prev_val, tau):
      alpha = 1 - np.exp(-DT_CTRL / tau) if tau > 0 else 1
      return alpha * val + (1 - alpha) * prev_val

    self.clamp_steer_ran = False   # set by the laneless branch below
    # a lane change (from the model, or the turn signals, which start before the model's desire and end after it)
    # stops the minimum-distance clamp; tracked every cycle so its hold-off also covers one made outside laneless
    clamp_lane_changing = model_v2.meta.desire != Desire.none or model_v2.meta.laneChangeState != LaneChangeState.off or \
                          CS.leftBlinker or CS.rightBlinker
    if not CC.latActive:
      new_desired_curvature = self.curvature
    elif self.lanefull_mode_enabled:
      if len(lat_plan.curvatures) == 0:
        new_desired_curvature = self.curvature
      else:
        curvature = get_lag_adjusted_curvature(self.CP, CS.vEgo, lat_plan.psis, lat_plan.curvatures, steer_actuator_delay + lat_smooth_seconds, lat_plan.distances)
        new_desired_curvature = smooth_value(curvature, self.desired_curvature, lat_smooth_seconds)
    else:
      # The minimum-distance clamp adds its correction to the model's curvature: zero unless the model's plan comes
      # too close to a line or the road edge (min_distance_clamp.py), and only from a valid, current model message.
      model_ok = self.sm.valid['modelV2'] and self.sm.alive['modelV2']
      clamp_kappa = self.clamp_steer.update(model_v2, CS.vEgo, clamp_lane_changing, self.clamp_settings, usable=model_ok)
      self.clamp_steer_ran = True
      new_desired_curvature = smooth_value(model_v2.action.desiredCurvature + clamp_kappa, self.desired_curvature, 0.1)
    if not self.clamp_steer_ran:
      # not steering laneless: no correction, but the lane change hold-off and the alert's end keep their history
      self.clamp_steer.update(model_v2, CS.vEgo, clamp_lane_changing, self.clamp_settings, usable=False)

    self.desired_curvature, curvature_limited = clip_curvature(CS.vEgo, self.desired_curvature, new_desired_curvature, lp.roll,
                                                               max_lat_accel_low=self.lat_accel_low,
                                                               max_lat_accel_high=self.lat_accel_high,
                                                               max_lat_jerk=self.lat_jerk_limit)

    actuators.curvature = float(self.desired_curvature)
    steer, steeringAngleDeg, lac_log = self.LaC.update(CC.latActive, CS, self.VM, lp,
                                                       self.steer_limited_by_safety, self.desired_curvature,
                                                       CC, curvature_limited,
                                                       model_data=self.sm['modelV2'])
    actuators.torque = float(steer)
    actuators.steeringAngleDeg = float(steeringAngleDeg)
    # Ensure no NaNs/Infs
    for p in ACTUATOR_FIELDS:
      attr = getattr(actuators, p)
      if not isinstance(attr, Number):
        continue

      if not math.isfinite(attr):
        cloudlog.error(f"actuators.{p} not finite {actuators.to_dict()}")
        setattr(actuators, p, 0.0)

    return CC, lac_log

  def publish(self, CC, lac_log):
    CS = self.sm['carState']

    # Orientation and angle rates can be useful for carcontroller
    # Only calibrated (car) frame is relevant for the carcontroller
    CC.currentCurvature = self.curvature
    if self.calibrated_pose is not None:
      CC.orientationNED = self.calibrated_pose.orientation.xyz.tolist()
      CC.angularVelocity = self.calibrated_pose.angular_velocity.xyz.tolist()

    #acceleration_value = list(self.sm['liveLocationKalman'].accelerationCalibrated.value)
    #if len(acceleration_value) > 2:
    #  if abs(acceleration_value[0]) > 16.0:
    #    print("Collision detected. disable openpilot, restart")
    #    self.params.put_bool("OpenpilotEnabledToggle", False)
    #    self.params.put_int("SoftRestartTriggered", 1)

    CC.cruiseControl.override = CC.enabled and not CC.longActive and self.CP.openpilotLongitudinalControl
    CC.cruiseControl.cancel = CS.cruiseState.enabled and (not CC.enabled or not self.CP.pcmCruise)

    desired_kph = min(CS.vCruiseCluster, self.sm['carrotMan'].desiredSpeed)
    setSpeed = float(desired_kph * CV.KPH_TO_MS)
    speeds = self.sm['longitudinalPlan'].speeds
    if len(speeds):
      CC.cruiseControl.resume = CC.enabled and CS.cruiseState.standstill and speeds[-1] > 0.1
      vCluRatio = CS.vCluRatio if CS.vCluRatio > 0.5 else 1.0
      setSpeed = speeds[-1] / vCluRatio

    hudControl = CC.hudControl

    hudControl.activeCarrot = self.sm['carrotMan'].activeCarrot
    hudControl.atcDistance = self.sm['carrotMan'].xDistToTurn

    lp = self.sm['longitudinalPlan']
    if self.CP.pcmCruise:
      speed_from_pcm = self.params.get_int("SpeedFromPCM")
      if speed_from_pcm == 1: #toyota
        hudControl.setSpeed = float(CS.vCruiseCluster * CV.KPH_TO_MS)
      elif speed_from_pcm == 2:
        hudControl.setSpeed = float(max(30/3.6, desired_kph * CV.KPH_TO_MS))
      elif speed_from_pcm == 3: # honda
        hudControl.setSpeed = setSpeed if lp.xState == 3 else float(desired_kph * CV.KPH_TO_MS)
      else:
        hudControl.setSpeed = float(max(30/3.6, setSpeed))
    else:
      hudControl.setSpeed = setSpeed if lp.xState == 3 else float(desired_kph * CV.KPH_TO_MS)
    hudControl.speedVisible = CC.enabled
    hudControl.lanesVisible = CC.enabled
    hudControl.leadVisible = self.sm['longitudinalPlan'].hasLead
    hudControl.leadDistanceBars = self.sm['selfdriveState'].personality.raw + 1
    hudControl.visualAlert = self.sm['selfdriveState'].alertHudVisual

    radarState = self.sm['radarState']
    leadOne = radarState.leadOne
    hudControl.leadDistance = leadOne.dRel if leadOne.status else 0
    hudControl.leadRelSpeed = leadOne.vRel if leadOne.status else 0
    hudControl.leadRadar = 1 if leadOne.radar else 0
    hudControl.leadDPath = leadOne.dPath

    meta = self.sm['modelV2'].meta
    if False: # command
      desire_map = {
        log.Desire.turnLeft: 1,
        log.Desire.turnRight: 2,
        log.Desire.laneChangeLeft: 3,
        log.Desire.laneChangeRight: 4,
      }
      hudControl.modelDesire = desire_map.get(meta.desire, 0)
    else: # model.
      hud_desire = 0
      if len(meta.desireState) > 4:
        if meta.desireState[1] > 0.1:
          hud_desire = 1   # turnLeft
        elif meta.desireState[2] > 0.1:
          hud_desire = 2   # turnRight
        elif meta.desireState[3] > 0.1:
          hud_desire = 3   # laneChangeLeft
        elif meta.desireState[4] > 0.1:
          hud_desire = 4   # laneChangeRight
      hudControl.modelDesire = hud_desire

    hudControl.rightLaneVisible = True
    hudControl.leftLaneVisible = True
    if self.sm.valid['driverAssistance']:
      hudControl.leftLaneDepart = self.sm['driverAssistance'].leftLaneDeparture
      hudControl.rightLaneDepart = self.sm['driverAssistance'].rightLaneDeparture

    if self.sm['selfdriveState'].active:
      CO = self.sm['carOutput']
      if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
        self.steer_limited_by_safety = abs(CC.actuators.steeringAngleDeg - CO.actuatorsOutput.steeringAngleDeg) > \
                                              STEER_ANGLE_SATURATION_THRESHOLD
      else:
        self.steer_limited_by_safety = abs(CC.actuators.torque - CO.actuatorsOutput.torque) > 1e-2

    # TODO: both controlsState and carControl valids should be set by
    #       sm.all_checks(), but this creates a circular dependency

    # controlsState
    dat = messaging.new_message('controlsState')
    dat.valid = CS.canValid
    cs = dat.controlsState

    cs.curvature = self.curvature
    cs.longitudinalPlanMonoTime = self.sm.logMonoTime['longitudinalPlan']
    cs.lateralPlanMonoTime = self.sm.logMonoTime['modelV2']
    cs.desiredCurvature = self.desired_curvature
    cs.longControlState = self.LoC.long_control_state
    cs.upAccelCmd = float(self.LoC.pid.p)
    cs.uiAccelCmd = float(self.LoC.pid.i)
    cs.ufAccelCmd = float(self.LoC.pid.f)
    cs.forceDecel = False
    if self.params.get_int("DisableDM") == 0:
      cs.forceDecel = bool((self.sm['driverMonitoringState'].alertLevel == log.DriverMonitoringState.AlertLevel.three) or
                           (self.sm['selfdriveState'].state == State.softDisabling))


    lat_tuning = self.CP.lateralTuning.which()
    if self.CP.steerControlType == car.CarParams.SteerControlType.angle:
      cs.lateralControlState.angleState = lac_log
    elif lat_tuning == 'pid':
      cs.lateralControlState.pidState = lac_log
    elif lat_tuning == 'torque':
      cs.lateralControlState.torqueState = lac_log

    cs.activeLaneLine = self.lanefull_mode_enabled
    # the clamp's ding: only while its laneless correction ran this cycle and steering is active (no stale True)
    cs.clampSteerActive = bool(self.clamp_steer_ran and self.clamp_steer.alert_active and CC.latActive)
    self.pm.send('controlsState', dat)

    # carControl
    cc_send = messaging.new_message('carControl')
    cc_send.valid = CS.canValid
    cc_send.carControl = CC
    self.pm.send('carControl', cc_send)

  def run(self):
    rk = Ratekeeper(100, print_delay_threshold=None)
    while True:
      self.update()
      CC, lac_log = self.state_control()
      self.publish(CC, lac_log)
      rk.monitor_time()


def main():
  config_realtime_process(4, Priority.CTRL_HIGH)
  controls = Controls()
  controls.run()


if __name__ == "__main__":
  main()
