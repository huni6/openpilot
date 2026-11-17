"""On-road eGPU badge selection (ported from carrot-wip's hud badge logic)."""
import pytest

from openpilot.selfdrive.ui.onroad.egpu_badge import badge_state, egpu_badge


def test_hidden_without_any_egpu_involvement():
  assert egpu_badge(False, False, False, False, False) is None
  assert egpu_badge(False, True, False, False, False) is None  # an installed model alone is not shown


@pytest.mark.parametrize('kwargs,expected', [
  (dict(present=True, compiled=False, loading=False, active=False, startup_failed=False), ('eGPU', 'not_compiled')),
  (dict(present=True, compiled=True, loading=False, active=False, startup_failed=False), ('eGPU', 'ready')),
  (dict(present=True, compiled=True, loading=True, active=False, startup_failed=False), ('eGPU', 'loading')),
  (dict(present=True, compiled=True, loading=False, active=True, startup_failed=False), ('eGPU', 'active')),
  (dict(present=True, compiled=True, loading=False, active=False, startup_failed=True), ('eGPU ERROR', 'error')),
  # Runtime state stays visible while the USB hub re-enumerates.
  (dict(present=False, compiled=True, loading=False, active=True, startup_failed=False), ('eGPU', 'active')),
  (dict(present=False, compiled=True, loading=True, active=False, startup_failed=False), ('eGPU', 'loading')),
  (dict(present=False, compiled=True, loading=False, active=False, startup_failed=True), ('eGPU ERROR', 'error')),
])
def test_runtime_states(kwargs, expected):
  assert egpu_badge(**kwargs) == expected


def test_failure_outranks_loading_and_active():
  assert badge_state(compiled=True, loading=True, active=True, startup_failed=True) == 'error'
  assert badge_state(compiled=False, loading=True, active=True, startup_failed=False) == 'loading'


def test_delivery_progress_shows_only_when_nothing_runs():
  net = ('eGPU NET', 'loading')
  assert egpu_badge(False, False, False, False, False, delivery=net) == net
  assert egpu_badge(True, True, False, False, False, delivery=('eGPU NEXT', 'ready')) == ('eGPU NEXT', 'ready')
  assert egpu_badge(True, True, False, True, False, delivery=net) == ('eGPU', 'active')
  assert egpu_badge(True, True, True, False, False, delivery=net) == ('eGPU', 'loading')
  assert egpu_badge(True, True, False, False, True, delivery=net) == ('eGPU ERROR', 'error')


def test_recovery_replaces_the_failure_until_the_egpu_runs_again():
  retry = ('eGPU RETRY', 'loading')
  assert egpu_badge(True, True, False, False, True, recovery=retry) == retry
  assert egpu_badge(True, True, False, False, True, recovery=('eGPU READY', 'ready')) == ('eGPU READY', 'ready')
  assert egpu_badge(True, True, False, True, False, recovery=retry) == ('eGPU', 'active')


def test_every_state_has_a_badge_color():
  pytest.importorskip('pyray')
  from pathlib import Path
  import ast
  source = (Path(__file__).parents[1] / 'onroad/jetlink_badge.py').read_text()
  colors = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Assign) and n.targets[0].id == 'COLORS')
  keys = {k.value for k in colors.value.keys}
  assert {'active', 'ready', 'loading', 'error', 'not_compiled'} <= keys


# ---- real UI state and HUDs (same harness as test_jetlink_badge.py) ----
from types import SimpleNamespace  # noqa: E402
from unittest.mock import Mock  # noqa: E402,TID251


@pytest.fixture
def ui(monkeypatch):
  capnp = pytest.importorskip('capnp')  # noqa: F841
  import sys
  from openpilot.selfdrive.ui.tests.test_jetlink_badge import load_ui_modules
  existing = set(sys.modules)
  yield load_ui_modules(monkeypatch)
  for name in set(sys.modules) - existing:
    if name.startswith(('openpilot.selfdrive.ui.', 'openpilot.system.ui.widgets')):
      sys.modules.pop(name, None)


def test_ui_update_reads_params_installation_and_recovery(ui, monkeypatch):
  from openpilot.common import egpu_recovery_status
  from openpilot.selfdrive.modeld import big_model_status
  state = ui.state.ui_state
  flags = {'UsbGpuPresent', 'UsbGpuStartupFailed'}
  state.params = SimpleNamespace(get_bool=lambda key: key in flags)
  state.usbgpu_compiled = True
  monkeypatch.setattr(big_model_status, 'read_big_model_status', lambda cache: {'state': 'installed'})
  monkeypatch.setattr(egpu_recovery_status, 'read_status', lambda: {'state': 'recovering'})
  state._egpu_update_time = float('-inf')
  state._update_egpu_status()
  assert (state.usbgpu_present, state.usbgpu_startup_failed, state.usbgpu_active) == (True, True, False)
  assert state.usbgpu_delivery_badge == ('eGPU NEXT', 'ready')
  assert state.egpu_badge == ('eGPU RETRY', 'loading')  # recovery outranks the old failure
  flags.clear()
  flags.update({'UsbGpuPresent', 'UsbGpuActive'})
  state._egpu_update_time = float('-inf')
  state._update_egpu_status()
  assert state.egpu_badge == ('eGPU', 'active')


def test_ui_update_is_limited_to_once_per_second_and_never_raises(ui, monkeypatch):
  state = ui.state.ui_state
  reads = []
  state.params = SimpleNamespace(get_bool=lambda key: reads.append(key) or False)
  for now in (100., 100.5, 101.0):
    monkeypatch.setattr(ui.state.time, 'monotonic', lambda now=now: now)
    state._update_egpu_status()
  assert len(reads) == 2 * 4
  state.params = SimpleNamespace(get_bool=Mock(side_effect=OSError()))
  state.egpu_badge = ('eGPU', 'active')
  monkeypatch.setattr(ui.state.time, 'monotonic', lambda: 200.)
  state._update_egpu_status()
  assert state.egpu_badge is None


def hud(ui, kind, monkeypatch):
  import pyray as rl
  module = getattr(ui, kind)
  renderer = object.__new__(module.HudRenderer)
  renderer._can_draw_top_icons = True
  renderer.is_cruise_available = False
  renderer._font_display = None
  renderer._exp_button = SimpleNamespace(render=Mock())
  renderer._plot_renderer = SimpleNamespace(draw=Mock())
  for name in ('_draw_set_speed', '_draw_steering_wheel', '_draw_date_time', '_draw_tpms_top_right'):
    setattr(renderer, name, Mock())
  monkeypatch.setattr(rl, 'draw_rectangle_gradient_v', Mock())
  calls = []

  def draw(status, bounds, font_size, **kwargs):
    calls.append((status, bounds, font_size))
    if status is None:
      return None
    width = 200  # right-aligned within bounds, as draw_jetlink_badge does
    return rl.Rectangle(bounds.x + bounds.width - width, bounds.y, width, bounds.height)
  monkeypatch.setattr(module, 'draw_jetlink_badge', draw)
  monkeypatch.setattr(module, 'draw_jetlink_detail', Mock())
  return renderer, calls


def test_wide_hud_puts_egpu_next_to_the_button_and_moves_jetson_left(ui, monkeypatch):
  import pyray as rl
  renderer, calls = hud(ui, 'wide', monkeypatch)
  ui.state.ui_state.egpu_badge = ('eGPU', 'active')
  ui.state.ui_state.jetlink_badge = ('jetSON READY', 'ready')
  renderer._render(rl.Rectangle(30, 20, 2160, 1080))
  (egpu, egpu_bounds, _), (jetson, jetson_bounds, _) = calls
  assert egpu == ('eGPU', 'active') and jetson == ('jetSON READY', 'ready')
  button_x = 30 + 2160 - ui.wide.UI_CONFIG.border_size - ui.wide.UI_CONFIG.button_size
  assert egpu_bounds.x + egpu_bounds.width == button_x - 24
  assert jetson_bounds.x + jetson_bounds.width == button_x - 24 - 200 - 16  # left of the eGPU badge
  assert egpu_bounds.y == jetson_bounds.y == 20 + 24


def test_wide_hud_without_egpu_keeps_the_jetson_badge_where_it_was(ui, monkeypatch):
  import pyray as rl
  renderer, calls = hud(ui, 'wide', monkeypatch)
  ui.state.ui_state.egpu_badge = None
  ui.state.ui_state.jetlink_badge = ('jetSON READY', 'ready')
  renderer._render(rl.Rectangle(30, 20, 2160, 1080))
  assert len(calls) == 1
  button_x = 30 + 2160 - ui.wide.UI_CONFIG.border_size - ui.wide.UI_CONFIG.button_size
  assert calls[0][1].x + calls[0][1].width == button_x - 24


def test_compact_hud_draws_egpu_at_the_top_right(ui, monkeypatch):
  import pyray as rl
  renderer, calls = hud(ui, 'compact', monkeypatch)
  ui.state.ui_state.egpu_badge = ('eGPU ERROR', 'error')
  renderer._render(rl.Rectangle(30, 20, 476, 240))
  ((status, bounds, font_size),) = calls
  assert status == ('eGPU ERROR', 'error') and font_size == 22
  assert bounds.x + bounds.width == 30 + 476 - 24 and bounds.y == 20 + 12
  assert bounds.y + bounds.height <= 20 + 100  # stays above the bottom-right Jetson column
  calls.clear()
  ui.state.ui_state.egpu_badge = None
  renderer._render(rl.Rectangle(30, 20, 476, 240))
  assert calls == []
