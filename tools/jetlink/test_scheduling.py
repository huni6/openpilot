import ast
import inspect
import sys
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from openpilot.selfdrive.modeld.jetlink import daemon


def test_boot_latch_runs_before_any_usb_session():
  tree = ast.parse(inspect.getsource(daemon.main))
  main = tree.body[0]
  latch = next(n for n in main.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
               and isinstance(n.value.func, ast.Name) and n.value.func.id == 'initialize_management')
  usb_calls = [n for n in ast.walk(main) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id in ('host_attached', 'setup_gadget')]
  assert usb_calls and all(latch.lineno < n.lineno for n in usb_calls)


@pytest.mark.parametrize('mode,unavailable,expected', [('usb', False, ('usb', True)),
                                                   ('auto', True, ('usb', False)), ('android', False, ('android', False))])
def test_actual_daemon_call_keeps_auto_fallback_bulk_only(mode, unavailable, expected):
  tree = ast.parse(inspect.getsource(daemon.main))
  call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'setup_gadget')
  actual = eval(compile(ast.Expression(call), '<gadget-call>', 'eval'),
                {'TRANSPORT_MODE': mode, 'mobile_unavailable': unavailable, 'setup_gadget': lambda mode, management: (mode, management)})
  assert actual == expected


@pytest.mark.parametrize('onroad,inference,big_online,realtime', [
  (False, False, False, False), (False, False, True, False),
  (True, False, True, False), (False, True, True, True),
  (False, True, False, False), (True, True, False, False),
])
def test_inference_deadlines_use_low_realtime_priority_without_waking_cores(monkeypatch, onroad, inference, big_online, realtime):
  monkeypatch.setitem(sys.modules, 'openpilot.common.params', NS(Params=lambda: NS(get_bool=lambda key: onroad)))
  monkeypatch.setattr(daemon, 'Path', lambda path: NS(read_text=lambda: '1' if big_online else '0',
                                                   iterdir=lambda: [NS(name='10'), NS(name='11')]))
  calls, affinities = [], []
  monkeypatch.setattr(daemon.os, 'SCHED_FIFO', 1, raising=False)
  monkeypatch.setattr(daemon.os, 'SCHED_OTHER', 0, raising=False)
  monkeypatch.setattr(daemon.os, 'sched_setscheduler', lambda pid, policy, priority: calls.append((pid, policy, priority)), raising=False)
  monkeypatch.setattr(daemon.os, 'sched_param', lambda value: value, raising=False)
  monkeypatch.setattr(daemon.os, 'sched_setaffinity', lambda tid, cores: affinities.append((tid, cores)), raising=False)
  daemon.update_affinity(inference=inference)
  assert calls == [(0, 1 if realtime else 0, 1 if realtime else 0)]
  assert affinities == [(10, {0, 1, 2, 3}), (11, {0, 1, 2, 3})]


@pytest.mark.parametrize('error', [ValueError, RuntimeError])
def test_disconnected_or_failed_client_restores_idle_policy(monkeypatch, error):
  from openpilot.selfdrive.modeld.jetlink.link import SPEC
  monkeypatch.setitem(sys.modules, 'openpilot.common.params', NS(Params=lambda: None))
  monkeypatch.setitem(sys.modules, 'openpilot.common.runtime_diagnostics', NS(RuntimeDiagnostics=lambda *a: None))
  monkeypatch.setitem(sys.modules, 'openpilot.common.swaglog', NS(cloudlog=NS(event=lambda *a: None)))
  connected, policies = [True], []
  monkeypatch.setattr(daemon, 'host_attached', lambda: connected[0])
  monkeypatch.setattr(daemon, 'publish', lambda *a, **kw: None)
  monkeypatch.setattr(daemon, 'update_affinity', lambda inference=False: policies.append(inference))
  class Connection:
    def settimeout(self, seconds):
      pass
    def __enter__(self):
      return self
    def __exit__(self, *args):
      pass
  def send(*args):
    connected[0] = False
    raise error('reply failed')
  monkeypatch.setattr(daemon, 'send', send)
  with pytest.raises(error) if error is RuntimeError else nullcontext():
    daemon._serve_local(NS(accept=lambda: (Connection(), None)), NS(spec=SPEC, last_state={}), {'protocol': 2}, None, None)
  assert policies[-2:] == [True, False]
