"""Failure isolation without publishing messages or touching vehicle controls."""
import ast
from collections import defaultdict
from functools import cache
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[2]
SELFDRIVED = ROOT / 'selfdrive/selfdrived/selfdrived.py'


def ignored_processes(device='mici', nvme=False):
  # Exercise the constructor's exact policy without opening IPC or real Params.
  tree = ast.parse(SELFDRIVED.read_text())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SelfdriveD')
  init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
  nodes = [n for n in init.body if 'self.ignored_processes' in ast.unparse(n)]
  context = {'self': NS(), 'HARDWARE': NS(get_device_type=lambda: device), 'os': NS(path=NS(exists=lambda _: nvme))}
  exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SELFDRIVED), 'exec'), context)
  return context['self'].ignored_processes


@pytest.mark.parametrize('device,nvme,expected', [
  ('mici', False, {'jetson_maintenance', 'comma_remote', 'carrot_cluster'}),
  ('tici', False, {'jetson_maintenance', 'comma_remote', 'carrot_cluster'}),
  ('tici', True, {'jetson_maintenance', 'comma_remote', 'carrot_cluster', 'loggerd'}),
])
def test_only_non_driving_helpers_are_added_to_existing_exception_policy(device, nvme, expected):
  assert ignored_processes(device, nvme) == expected


@cache
def validity_expression(function='fill_model_msg'):
  tree = ast.parse((ROOT / 'selfdrive/modeld/modeld.py').read_text())
  call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == function)
  assert isinstance(call.args[-1], ast.Name) and call.args[-1].id == 'model_output_valid'
  assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'model_output_valid' for t in n.targets))
  return compile(ast.Expression(assignment.value), '<model-validity>', 'eval')


def output_valid(model, calibrated=True):
  return eval(validity_expression(), {'model': model, 'live_calib_seen': calibrated})


@pytest.mark.parametrize('function', ['fill_model_msg', 'fill_pose_msg'])
def test_published_model_validity_keeps_calibration_and_jetlink_fault_gates(function):
  for calibrated in (False, True):
    scope = {'live_calib_seen': calibrated, 'model': NS()}
    assert eval(validity_expression(function), scope) is calibrated
    for valid in (False, True):
      scope['model'] = NS(output_valid=valid)
      assert eval(validity_expression(function), scope) is (valid and calibrated)


def make_selfdrived():
  # These integration tests run on comma, where the native IPC module is built.
  # All messages remain in memory; no SubMaster, PubMaster, or Params is created.
  pytest.importorskip('msgq.ipc_pyx')
  import cereal.messaging as messaging
  from cereal import car, log
  from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
  from openpilot.selfdrive.selfdrived.events import Events

  class Messages(dict):
    def all_alive(self, *args): return all(self.alive.values())
    def all_freq_ok(self, *args): return all(self.freq_ok.values())
    def all_checks(self): return self.all_alive() and self.all_freq_ok() and all(self.valid.values())

  sm = Messages()
  for name in ('controlsState', 'deviceState', 'peripheralState', 'liveCalibration', 'modelV2',
               'carrotMan', 'carControl', 'longitudinalPlan', 'livePose', 'radarState', 'driverAssistance'):
    sm[name] = getattr(messaging.new_message(name), name)
  sm['pandaStates'] = []
  sm['managerState'] = NS(processes=[])
  sm['controlsState'].lateralControlState.init('pidState')
  sm['deviceState'].freeSpacePercent = 90
  sm['liveCalibration'].calStatus = log.LiveCalibrationData.Status.calibrated
  sm.frame = 100
  sm.valid = defaultdict(lambda: True)
  sm.alive = defaultdict(lambda: True)
  sm.freq_ok = defaultdict(lambda: True)
  sm.updated = defaultdict(lambda: False)
  sm.recv_frame = defaultdict(lambda: 1)

  sd = object.__new__(SelfdriveD)
  sd.sm, sd.events = sm, Events()
  sd.CP = car.CarParams.new_message(notCar=True, pcmCruise=True)
  sd.CS_prev = car.CarState.new_message()
  sd.car_events = NS(update=lambda *a: Events())
  sd.params = NS(get_int=lambda _: 0, get_bool=lambda _: False)
  sd.initialized = sd.enabled = True
  sd.startup_event = sd.calibrated_pose = sd.not_running_prev = sd.logged_comm_issue = None
  sd.is_ldw_enabled = sd.excessive_actuation = False
  sd.mismatch_counter = sd.cruise_mismatch_counter = sd.last_steering_pressed_frame = 0
  sd.last_functional_fan_frame = sd.distance_traveled = 0
  sd.atc_type_last = ''
  sd.sensor_packets = sd.camera_packets = []
  sd.gps_location_service = 'gpsLocationExternal'
  sd.ignored_processes = ignored_processes()
  sd.rk = NS(lagging=False)
  cs = car.CarState.new_message(canValid=True, standstill=True)
  return sd, cs


@pytest.mark.parametrize('failed,critical', [
  ({'comma_remote'}, False), ({'jetson_maintenance'}, False),
  ({'comma_remote', 'jetson_maintenance'}, False),
  ({'carrot_cluster'}, False), ({'carrot_cluster', 'modeld'}, True),
  ({'jetlinkd'}, True), ({'modeld'}, True), ({'controlsd'}, True), ({'pandad'}, True),
  ({'comma_remote', 'modeld'}, True),
])
def test_real_selfdrived_process_events_and_disengagement(monkeypatch, failed, critical):
  sd, cs = make_selfdrived()
  from cereal import log
  from openpilot.selfdrive.modeld.jetlink import link
  from openpilot.selfdrive.selfdrived.state import StateMachine, SOFT_DISABLE_TIME
  from openpilot.common.realtime import DT_CTRL
  monkeypatch.setattr(link, 'fault_active', lambda: False)
  sd.sm['managerState'].processes = [NS(name=name, running=False, shouldBeRunning=True) for name in failed]
  sd.update_events(cs)
  assert (log.OnroadEvent.EventName.processNotRunning in sd.events.names) is critical
  machine = StateMachine()
  machine.state = log.SelfdriveState.OpenpilotState.enabled
  for _ in range(int(SOFT_DISABLE_TIME / DT_CTRL) + 2):
    machine.update(sd.events)
  assert machine.state == (log.SelfdriveState.OpenpilotState.disabled if critical else log.SelfdriveState.OpenpilotState.enabled)


def test_model_ipc_fault_still_disengages_when_marker_cannot_be_read(monkeypatch, tmp_path):
  sd, cs = make_selfdrived()
  from cereal import log
  from openpilot.selfdrive.modeld.jetlink import link, model
  from openpilot.selfdrive.selfdrived.state import StateMachine, SOFT_DISABLE_TIME
  from openpilot.common.realtime import DT_CTRL
  monkeypatch.setattr(link, 'FAULT', tmp_path)  # Unreadable as a file.
  assert not link.fault_active()
  m = object.__new__(model.JoiningModel)
  now = [100.]
  m.fault_until = 100. + link.FAULT_HOLD_SECONDS
  monkeypatch.setattr(model.time, 'monotonic', lambda: now[0])
  machine = StateMachine()
  machine.state = log.SelfdriveState.OpenpilotState.enabled
  for step in range(int(SOFT_DISABLE_TIME / DT_CTRL) + 2):
    now[0] = 100. + step * DT_CTRL
    sd.sm.valid['modelV2'] = output_valid(m)
    sd.update_events(cs)
    assert log.OnroadEvent.EventName.commIssue in sd.events.names
    machine.update(sd.events)
  assert machine.state == log.SelfdriveState.OpenpilotState.disabled
  now[0] = m.fault_until
  sd.sm.valid['modelV2'] = output_valid(m)
  sd.update_events(cs)
  assert log.OnroadEvent.EventName.commIssue not in sd.events.names
  machine.update(sd.events)
  assert machine.state == log.SelfdriveState.OpenpilotState.disabled  # No automatic re-engagement.
