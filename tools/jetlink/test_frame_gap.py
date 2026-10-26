"""Frame-gap output semantics with inert inference, no GPU or vehicle IPC."""
import ast
import json
from pathlib import Path
import socket
import threading
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

from cereal import log
from cereal.services import SERVICE_LIST
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.common.utils import MovingAverage
from openpilot.selfdrive.modeld.fill_model_msg import fill_model_msg, fill_pose_msg, PublishState
from openpilot.selfdrive.modeld.jetlink import link, model
from openpilot.selfdrive.modeld.jetlink.contracts import parse_outputs
from openpilot.selfdrive.modeld.parse_model_outputs import Parser
from test_hotplug_resources import joining_model

ROOT = Path(__file__).resolve().parents[2]


def inputs():
  return {'desire_pulse': np.zeros(8), 'traffic_convention': [1, 0], 'action_t': [.2, .3]}


def publish_output(value, sent):
  # Exercise modeld's actual publication condition and send calls, without IPC.
  tree = ast.parse((ROOT / 'selfdrive/modeld/modeld.py').read_text())
  gate = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and ast.unparse(n.test) == 'model_output is not None')
  sends = [n for n in gate.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
           and ast.unparse(n.value.func) == 'pm.send']
  assert len(sends) == 3
  gate.body = sends
  exec(compile(ast.fix_missing_locations(ast.Module(body=[gate], type_ignores=[])), '<publication-gate>', 'exec'),
       {'model_output': value, 'pm': NS(send=lambda name, _: sent.append(name)),
        'modelv2_send': object(), 'drivingdata_send': object(), 'posenet_send': object()})


@pytest.mark.parametrize('frame_ids', [
  [13955, 13956, 13957, 13958, 13960, 13961],  # Recorded route254 segment 11.
  [17867, 17868, 17869, 17870, 17872, 17873],  # Recorded route254 segment 14.
  [10, 11, 12, 13],
  [10, 12, 14, 16, 17],
  [10] + list(range(12, 174, 2)) + [173],
])
def test_gaps_update_remote_history_but_do_not_publish(monkeypatch, frame_ids):
  m = joining_model(monkeypatch)
  calls = []
  m.active = True

  def infer(images, packed, frame, reset, source_sof):
    # Host-managed feature history must advance even on unpublished frames.
    if 'prev_feat' in m.views:
      assert np.all(m.views['prev_feat'] == frame - 1)
    calls.append((frame, reset, source_sof))
    return np.full(m.spec.output_nelem, frame, np.float32)

  m.client = NS(infer=infer)
  m.small.run = lambda *a: pytest.fail('healthy remote must not fall back')
  previous = frame_ids[0] - 1
  published = []
  skipped = []
  for frame_id in frame_ids:
    dropped = max(0, frame_id - previous - 1)
    m.source_sof = frame_id * 50_000_000
    result = m.run({}, {}, inputs(), dropped > 0)
    sent = []
    publish_output(result, sent)
    if dropped:
      assert result is None and sent == []
      skipped.append(frame_id)
    else:
      assert result == 'external'
      assert sent == ['modelV2', 'drivingModelData', 'cameraOdometry']
      published.append(frame_id)
    assert m.active and m.output_valid
    previous = frame_id
  assert len(calls) == len(frame_ids)
  assert [frame for frame, _, _ in calls] == list(range(1, len(frame_ids) + 1))
  assert [reset for _, reset, _ in calls] == [True] + [False] * (len(frame_ids) - 1)
  assert [sof for _, _, sof in calls] == [frame * 50_000_000 for frame in frame_ids]
  assert published[-1] == frame_ids[-1]
  assert len(published) + len(skipped) == len(frame_ids)


@pytest.mark.parametrize('prepare_only', [False, True])
@pytest.mark.parametrize('failure', ['transport', 'parse'])
@pytest.mark.parametrize('marker_fails', [False, True])
def test_real_failures_still_latch_fault_on_prepare_only(monkeypatch, tmp_path, prepare_only, failure, marker_fails):
  m = joining_model(monkeypatch)
  m.active = True
  now = [100.]
  monkeypatch.setattr(model.time, 'monotonic', lambda: now[0])
  fault = tmp_path if marker_fails else tmp_path / 'fault'
  monkeypatch.setattr(model, 'FAULT', fault)
  monkeypatch.setattr(link, 'FAULT', fault)
  calls = []

  def fail(*a, **kw):
    raise TimeoutError('transport timed out') if failure == 'transport' else ValueError('invalid model result')

  m.client = NS(infer=fail if failure == 'transport' else lambda *a, **kw: np.ones(m.spec.output_nelem),
                close=lambda: calls.append('close'))
  if failure == 'parse':
    monkeypatch.setattr(model, 'parse_outputs', fail)
  def local(bufs, transforms, values, prepare):
    calls.append(('local', prepare))
    return None if prepare else 'local'
  m.small.run = local
  m.small.reset = lambda: calls.append('reset')
  result = m.run({}, {}, inputs(), prepare_only)
  assert result == (None if prepare_only else 'local')
  assert calls == ['close', 'reset', ('local', prepare_only)]
  assert m.client is None and not m.active and not m.output_valid
  assert m.fault_until == m.next_join == now[0] + link.FAULT_HOLD_SECONDS
  assert link.fault_active() is (not marker_fails)
  # A skipped publication cannot clear the fault on the next native frame.
  now[0] += .05
  assert m.run({}, {}, inputs(), False) == 'local'
  assert not m.output_valid


@pytest.mark.parametrize('prepare_only', [False, True])
def test_local_path_keeps_prepare_only_contract(monkeypatch, prepare_only):
  m = joining_model(monkeypatch)
  m.join_allowed = False
  calls = []
  def local(bufs, transforms, values, prepare):
    calls.append(prepare)
    return None if prepare else 'local'
  m.small.run = local
  assert m.run({}, {}, inputs(), prepare_only) == (None if prepare_only else 'local')
  assert calls == [prepare_only]
  assert m.small_runs == (3 if prepare_only else 4)


def message_monitor():
  # Use the real Python health checks, but never construct an IPC subscriber.
  tree = ast.parse((ROOT / 'cereal/messaging/__init__.py').read_text())
  classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in ('FrequencyTracker', 'SubMaster')]
  scope = {'SERVICE_LIST': SERVICE_LIST, 'MovingAverage': MovingAverage}
  future = ast.parse('from __future__ import annotations').body
  exec(compile(ast.Module(body=future + classes, type_ignores=[]), '<message-health>', 'exec'), scope)
  sm = object.__new__(scope['SubMaster'])
  sm.services = ['modelV2', 'cameraOdometry']
  sm.static_freq_services = set(sm.services)
  sm.frame, sm.simulation = -1, False
  for field in ('seen', 'updated', 'alive', 'freq_ok', 'valid'):
    setattr(sm, field, dict.fromkeys(sm.services, False))
  for field in ('recv_time', 'recv_frame', 'logMonoTime'):
    setattr(sm, field, dict.fromkeys(sm.services, 0))
  sm.data = {}
  sm.ignore_average_freq = sm.ignore_alive = sm.ignore_valid = []
  sm.freq_tracker = {s: scope['FrequencyTracker'](SERVICE_LIST[s].frequency, 100, False) for s in sm.services}
  return sm


def received_messages(when, valid=True):
  return [NS(which=lambda s=s: s, logMonoTime=int(when * 1e9), valid=valid, **{s: NS()})
          for s in ('modelV2', 'cameraOdometry')]


def communication_events(sm):
  tree = ast.parse((ROOT / 'selfdrive/selfdrived/selfdrived.py').read_text())
  gate = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
              and ast.unparse(n.test) == 'not self.sm.all_checks() and no_system_errors')
  gate.body = [gate.body[0]]  # Keep exact event selection, omit diagnostic logging.
  events = set()
  exec(compile(ast.Module(body=[gate], type_ignores=[]), '<communication-events>', 'exec'),
       {'self': NS(sm=sm, events=NS(add=events.add)), 'no_system_errors': True,
        'EventName': NS(commIssue='commIssue', commIssueAvgFreq='commIssueAvgFreq')})
  return events


def test_skipped_outputs_keep_stale_and_invalid_message_detection():
  sm = message_monitor()
  for tick in range(1, 31):
    sm.update_msgs(tick * .05, received_messages(tick * .05))
  assert sm.all_checks() and not communication_events(sm)

  # Camera wait plus one prepare-only output: no fabricated fresh message.
  for now in (1.55, 1.60):
    sm.update_msgs(now, [])
    assert sm.all_checks() and not communication_events(sm)
    assert sm.recv_time == dict.fromkeys(sm.services, 1.5)
  sm.update_msgs(1.65, received_messages(1.65))
  assert sm.all_checks() and not communication_events(sm)

  sm.update_msgs(1.70, received_messages(1.70, valid=False))
  assert not sm.all_valid() and communication_events(sm) == {'commIssue'}
  sm.update_msgs(1.75, received_messages(1.75))
  assert sm.all_checks()

  # Persistent prepare-only returns must let the unchanged liveness timer expire.
  for tick in range(1, 12):
    sm.update_msgs(1.75 + tick * .05, [])
  assert sm.recv_time == dict.fromkeys(sm.services, 1.75)
  assert not sm.all_alive() and communication_events(sm) == {'commIssue'}


def test_repeated_isolated_gaps_still_raise_average_frequency_event():
  sm = message_monitor()
  for tick in range(1, 301):
    sm.update_msgs(tick * .05, received_messages(tick * .05))
  assert sm.all_checks()

  for tick in range(301, 701):
    now = tick * .05
    sm.update_msgs(now, [] if tick % 2 else received_messages(now))
    assert sm.all_alive() and sm.all_valid()
  # Both long and short frequency windows must expire; one miss is not a fault.
  assert not sm.all_freq_ok()
  assert communication_events(sm) == {'commIssueAvgFreq'}
  for tick in range(701, 1001):
    sm.update_msgs(tick * .05, received_messages(tick * .05))
  assert sm.all_checks() and not communication_events(sm)


@pytest.mark.parametrize('calibrated, output_valid', [(True, True), (False, True), (True, False)])
def test_recovered_output_keeps_drop_statistics_and_validity_gates(monkeypatch, calibrated, output_valid):
  m = joining_model(monkeypatch)
  monkeypatch.setattr(model, 'parse_outputs', parse_outputs)
  m.parser = Parser()
  m.fault_until = 0. if output_valid else float('inf')
  m.client = NS(infer=lambda *a, **kw: np.zeros(m.spec.output_nelem, np.float32))
  tree = ast.parse((ROOT / 'selfdrive/modeld/modeld.py').read_text())
  # Execute the actual loop's counters, including the update outside its output gate.
  loop = next(n for n in ast.walk(tree) if isinstance(n, ast.While) and any(
    isinstance(s, ast.Assign) and ast.unparse(s.targets[0]) == 'vipc_dropped_frames' for s in n.body))
  start = next(i for i, n in enumerate(loop.body) if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == 'vipc_dropped_frames')
  end = next(i for i, n in enumerate(loop.body) if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == 'prepare_only')
  counters = compile(ast.Module(body=loop.body[start:end+1], type_ignores=[]), '<drop-counters>', 'exec')
  advance = next(n for n in loop.body if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == 'last_vipc_frame_id')
  advance = compile(ast.Module(body=[advance], type_ignores=[]), '<advance-frame>', 'exec')
  validity = next(n for n in ast.walk(loop) if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == 'model_output_valid')
  validity = compile(ast.Module(body=[validity], type_ignores=[]), '<output-validity>', 'exec')
  state = dict(frame_dropped_filter=FirstOrderFilter(0., 10., .05), run_count=20, last_vipc_frame_id=99,
               live_calib_seen=calibrated, model=m)
  sent_frames = []
  for frame in (100, 102, 103):
    state['meta_main'] = NS(frame_id=frame)
    exec(counters, state)
    result = m.run({}, {}, inputs(), state['prepare_only'])
    sent = []
    publish_output(result, sent)
    if frame == 102:
      assert result is None and sent == []
    else:
      exec(validity, state)
      msg, pose = log.Event.new_message(), log.Event.new_message()
      msg.init('modelV2')
      pose.init('cameraOdometry')
      fill_model_msg(msg, result, log.ModelDataV2.Action.new_message(), PublishState(), frame, frame, frame,
                     state['frame_drop_ratio'], frame * 50_000_000, .04, state['model_output_valid'])
      fill_pose_msg(pose, result, frame, state['vipc_dropped_frames'], frame * 50_000_000, state['model_output_valid'])
      assert msg.valid == pose.valid == (calibrated and output_valid)
      assert (msg.modelV2.frameDropPerc > 0) == (frame == 103)
      assert msg.modelV2.frameId == pose.cameraOdometry.frameId == frame
      sent_frames.append(frame)
    exec(advance, state)
  assert sent_frames == [100, 103] and state['last_vipc_frame_id'] == 103


@pytest.mark.parametrize('failure', ['frame', 'nan', 'length', 'timeout'])
def test_prepare_only_real_socket_fault_still_falls_back(monkeypatch, tmp_path, failure):
  if not hasattr(socket, 'AF_UNIX'):
    pytest.skip('Unix sockets unavailable')
  m = joining_model(monkeypatch)
  m.active = True
  m.warp = lambda *a: np.zeros(m.spec.warped_shape, np.uint8)
  m.warp.ready = True
  fault = tmp_path / 'fault'
  monkeypatch.setattr(model, 'FAULT', fault)
  monkeypatch.setattr(link, 'FAULT', fault)
  calls, requests, errors = [], [], []
  m.small.reset = lambda: calls.append('reset')
  m.small.run = lambda *args: calls.append(('local', args[-1]))
  path = str(tmp_path / 's')
  listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
  listener.bind(path)
  listener.listen(1)
  listener.settimeout(2)
  release = threading.Event()

  def server():
    try:
      with listener.accept()[0] as conn:
        conn.settimeout(2)
        link.send(conn, json.dumps({'spec': m.spec.to_dict()}).encode())
        request = link.receive(conn)
        frame, reset, source_sof = link.REQUEST.unpack_from(request)
        requests.append((frame, reset, source_sof))
        if failure == 'timeout':
          release.wait(2)
          return
        output = np.zeros(m.spec.output_nelem, np.float32)
        if failure == 'nan':
          output[100] = np.nan
        payload = link.REPLY.pack(frame + int(failure == 'frame'), 0, 0, 0) + output.tobytes()
        link.send(conn, payload[:-4] if failure == 'length' else payload)
    except Exception as exc:
      errors.append(exc)

  worker = threading.Thread(target=server)
  worker.start()
  client = None
  try:
    client = link.Client(path, timeout=1)
    m.client = client
    before = time.monotonic()
    assert m.run({}, {}, inputs(), True) is None
    after = time.monotonic()
    assert m.client is None and not m.active and not m.output_valid
    assert before + link.FAULT_HOLD_SECONDS <= m.fault_until <= after + link.FAULT_HOLD_SECONDS
    assert m.next_join == m.fault_until and link.fault_active()
    assert calls == ['reset', ('local', True)]
    assert requests == [(1, 1, 0)]
    assert not errors
  finally:
    release.set()
    if client is not None:
      client.close()
    worker.join(timeout=3)
    listener.close()
  assert not worker.is_alive() and not errors
