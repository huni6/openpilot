import sys
import subprocess
import textwrap
import threading
from concurrent.futures import Future
from types import SimpleNamespace as NS

import numpy as np
import pytest

from openpilot.selfdrive.modeld.jetlink import link, model


def test_handshake_worker_moves_off_realtime_model_core(monkeypatch):
  calls = []
  monkeypatch.setattr(link.os, 'sched_setscheduler', lambda *args: calls.append('normal'), raising=False)
  monkeypatch.setattr(link.os, 'SCHED_OTHER', 0, raising=False)
  monkeypatch.setattr(link.os, 'sched_param', lambda priority: priority, raising=False)
  monkeypatch.setattr(link.os, 'sched_setaffinity', lambda pid, cpus: calls.append(cpus), raising=False)
  client = NS(warmup=lambda cancelled: calls.append('warmup'), close=lambda: None)
  monkeypatch.setattr(link, 'Client', lambda: client)
  future = Future()
  link.ClientConnection._connect(future)
  assert future.result() is client
  assert calls == ['normal', {0, 1, 2}, 'warmup']


@pytest.mark.parametrize('cancel', [False, True])
def test_handshake_wait_does_not_block_caller_and_cancel_closes_socket(monkeypatch, cancel):
  entered, release, closed = threading.Event(), threading.Event(), threading.Event()

  def connect():
    entered.set()
    assert release.wait(2)
    return NS(close=closed.set, warmup=lambda cancelled: None)

  monkeypatch.setattr(link, 'Client', connect)
  connection = link.ClientConnection()
  try:
    assert entered.wait(1)
    assert not connection.future.done()
    if cancel:
      connection.close()
    release.set()
    if cancel:
      assert closed.wait(1)
    else:
      assert connection.future.result(timeout=1).close == closed.set
      connection.close()
      assert closed.is_set()
  finally:
    release.set()


def test_handshake_warmup_failure_closes_client_and_preserves_error(monkeypatch):
  closed = threading.Event()
  def warmup(cancelled):
    raise TimeoutError('warmup timed out')
  monkeypatch.setattr(link, 'Client', lambda: NS(warmup=warmup, close=closed.set))
  connection = link.ClientConnection()
  with pytest.raises(TimeoutError, match='warmup timed out'):
    connection.future.result(timeout=1)
  assert closed.wait(1)


def test_handshake_warmup_uses_resets_and_obeys_cancellation():
  client = object.__new__(link.Client)
  client.spec = link.SPEC
  calls = []
  client.infer = lambda images, packed, frame, reset: calls.append((images, packed.copy(), frame, reset))
  client.warmup(lambda: len(calls) == 2)
  assert len(calls) == 2
  for images, packed, frame, reset in calls:
    assert images.shape == link.SPEC.warped_shape and images.dtype == np.uint8
    assert not images.any() and reset and frame in (0, 1)
    assert packed[8] == 1 and np.count_nonzero(packed) == 1


def joining_model(monkeypatch):
  monkeypatch.setitem(sys.modules, 'openpilot.common.swaglog', NS(cloudlog=NS(warning=lambda *a: None, exception=lambda *a: None)))
  m = object.__new__(model.JoiningModel)
  m.client = m.connection = None
  m.preparation = None
  m.warp_size = (1344, 760)
  m.warp_inputs = object()
  m.small_runs = 3
  m.ready = m.join_allowed = True
  m.next_join = m.next_status = 0
  m.active = False
  m.fault_until = 0.
  m.error = ''
  m.small = NS(run=lambda *a: 'local')
  m.packed = np.zeros(link.SPEC.packed_nelem, np.float32)
  m.spec = link.SPEC
  m.reset = True
  m.prev_desire = np.zeros(8, np.float32)
  m.views = {name: a.reshape(shape) for (name, shape), a in zip(
    link.SPEC.packed_shapes.items(), np.split(m.packed, np.cumsum(link.SPEC.packed_sizes[:-1])), strict=True)}
  m.frame = m.source_sof = 0
  m.last_slow_log = 0
  m.warp = lambda *a: None
  m.warp.ready = True
  m.parser = NS(parse_outputs=lambda _: 'external')
  monkeypatch.setattr(model, 'parse_outputs', lambda parser, spec, result: parser.parse_outputs(result))
  return m


def test_pending_connection_keeps_local_frames_and_rechecks_join_permission(monkeypatch):
  m = joining_model(monkeypatch)
  future = Future()
  closed = []
  attempt = NS(future=future, close=lambda: closed.append(True))
  monkeypatch.setattr(model, 'ClientConnection', lambda: attempt)
  for _ in range(5):
    assert m.run({}, {}, {}, False) == 'local'
  assert m.connection is attempt and m.small_runs == 8
  future.set_result(NS())
  m.join_allowed = False
  assert m.run({}, {}, {}, False) == 'local'
  assert closed == [True] and m.connection is None and m.client is None


def test_cancelled_handshake_cleanup_cannot_interrupt_native_inference(monkeypatch):
  m = joining_model(monkeypatch)
  def close():
    raise OSError('socket cleanup failed')
  m.connection = NS(close=close)
  m.join_allowed = False
  assert m.run({}, {}, {}, False) == 'local'
  assert m.connection is None and m.client is None and m.output_valid


def test_completed_handshake_still_resets_external_history_and_recovers_on_failure(monkeypatch, tmp_path):
  m = joining_model(monkeypatch)
  calls = []
  output = np.ones(link.SPEC.output_nelem, np.float32)

  def infer(images, packed, frame, reset, source_sof):
    calls.append((frame, reset, packed.copy()))
    if len(calls) == 2:
      raise ConnectionError('unplugged')
    return output

  client = NS(spec=link.SPEC, infer=infer, close=lambda: calls.append('closed'))
  future = Future()
  future.set_result(client)
  m.connection = NS(future=future)
  m.small.reset = lambda: calls.append('reset')
  fault = tmp_path / 'fault'
  monkeypatch.setattr(model, 'FAULT', fault)
  inputs = {'desire_pulse': np.zeros(8), 'traffic_convention': [1, 0], 'action_t': [.2, .3]}
  assert m.run({}, {}, inputs, False) == 'external'
  assert calls[0][0:2] == (1, True)
  assert not calls[0][2][12:].any()
  assert m.active and m.client is client
  assert m.run({}, {}, inputs, False) == 'local'
  assert calls[-2:] == ['closed', 'reset'] and fault.exists()
  assert m.client is None and not m.active and m.next_join > 0
  assert not m.output_valid


@pytest.mark.parametrize('close_fails', [False, True])
@pytest.mark.parametrize('marker_fails', [False, True])
def test_cleanup_and_marker_failures_preserve_native_fallback_and_disengagement(monkeypatch, tmp_path, close_fails, marker_fails):
  m = joining_model(monkeypatch)
  now = [100.]
  monkeypatch.setattr(model.time, 'monotonic', lambda: now[0])
  calls = []

  def infer(*a, **kw):
    raise ConnectionError('unplugged')

  def close():
    calls.append('closed')
    if close_fails:
      raise OSError('socket cleanup failed')

  m.active = True
  m.client = NS(infer=infer, close=close)
  m.small.reset = lambda: calls.append('reset')
  fault = tmp_path if marker_fails else tmp_path / 'fault'
  monkeypatch.setattr(model, 'FAULT', fault)
  monkeypatch.setattr(link, 'FAULT', fault)
  inputs = {'desire_pulse': np.zeros(8), 'traffic_convention': [1, 0], 'action_t': [.2, .3]}
  assert m.output_valid
  assert m.run({}, {}, inputs, False) == 'local'
  assert calls == ['closed', 'reset']
  assert m.client is None and not m.active and m.error == 'unplugged'
  assert m.next_join == m.fault_until == 100. + link.FAULT_HOLD_SECONDS
  assert link.fault_active() is not marker_fails
  for elapsed in (0., 3., link.FAULT_HOLD_SECONDS - .001):
    now[0] = 100. + elapsed
    assert m.run({}, {}, {}, False) == 'local'
    assert not m.output_valid
  now[0] = 100. + link.FAULT_HOLD_SECONDS
  assert m.output_valid
  assert not link.fault_active()


def test_failed_native_reset_is_not_hidden(monkeypatch, tmp_path):
  m = joining_model(monkeypatch)
  m.active = True
  def fail(*a, **kw):
    raise RuntimeError('reset failed')
  m.client = NS(infer=fail, close=lambda: None)
  m.small.reset = fail
  m.small.run = lambda *a: pytest.fail('must not run stale native state')
  monkeypatch.setattr(model, 'FAULT', tmp_path / 'fault')
  inputs = {'desire_pulse': np.zeros(8), 'traffic_convention': [1, 0], 'action_t': [.2, .3]}
  with pytest.raises(RuntimeError, match='reset failed'):
    m.run({}, {}, inputs, False)
  assert not m.output_valid and m.client is None and not m.active


def test_failed_handshake_keeps_local_model_and_retry_delay(monkeypatch):
  m = joining_model(monkeypatch)
  future = Future()
  future.set_exception(ValueError('wrong model'))
  monkeypatch.setattr(model, 'ClientConnection', lambda: NS(future=future))
  assert m.run({}, {}, {}, False) == 'local'
  assert m.error == 'wrong model' and m.next_join > 0 and m.client is None


def test_native_fallback_queues_are_reset_in_place(monkeypatch):
  m = joining_model(monkeypatch)
  copied = []
  queue = NS(numel=lambda: 4, dtype=NS(itemsize=4), _buffer=lambda: NS(copyin=lambda b: copied.append(bytes(b))))
  values = np.ones(8, np.float32)
  m.small = NS(prev_desire=values, npy={'prev_feat': values.copy()}, input_queues={'feat_q': queue, 'tfm': object()})
  m._reset_small()
  assert m.small.prev_desire is values and not values.any()
  assert not m.small.npy['prev_feat'].any() and copied == [bytes(16)]


def test_daemon_freezes_startup_graph_but_new_session_cycles_are_collected():
  # Isolate the real GC change from pytest's own object graph. Stop main before
  # it touches scheduling, Params, USB, sockets, or vehicle paths.
  script = textwrap.dedent('''
    import gc, sys, weakref
    from types import SimpleNamespace
    if sys.platform == 'win32':
      sys.modules['fcntl'] = SimpleNamespace()
    from openpilot.selfdrive.modeld.jetlink import daemon
    class Cycle:
      def __init__(self): self.cycle = self
    class Stop(Exception): pass
    def stop(*args): raise Stop
    old = Cycle()
    old_ref = weakref.ref(old)
    daemon.os.sched_setscheduler = stop
    daemon.os.SCHED_OTHER = 0
    daemon.os.sched_param = lambda value: value
    try: daemon.main()
    except Stop: pass
    assert not gc.isenabled() and gc.get_freeze_count() > 0
    new = Cycle()
    new_ref = weakref.ref(new)
    del new, old
    gc.collect()
    assert old_ref() is not None and new_ref() is None
  ''')
  subprocess.run([sys.executable, '-c', script], check=True, timeout=15, capture_output=True)
