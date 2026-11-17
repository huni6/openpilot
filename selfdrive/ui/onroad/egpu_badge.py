"""On-road eGPU status badge, shared by the comma 3/3X and comma 4 HUDs.

Ported from carrot-wip (hud_renderer._draw_egpu_badge and
system/hardware/usbgpu.usbgpu_badge_state). This branch draws the Jetson
status as its own badge, so it is not merged into this one.
"""
from __future__ import annotations


def badge_state(compiled: bool, loading: bool, active: bool, startup_failed: bool) -> str:
  """Prioritized runtime state: failure, then loading, then running, then installation."""
  if startup_failed:
    return 'error'
  if loading:
    return 'loading'
  if active:
    return 'active'
  if not compiled:
    return 'not_compiled'
  return 'ready'


def egpu_badge(present: bool, compiled: bool, loading: bool, active: bool, startup_failed: bool,
               delivery: tuple[str, str] | None = None, recovery: tuple[str, str] | None = None) -> tuple[str, str] | None:
  """(text, state) to draw, or None while no eGPU is involved.

  Runtime state stays visible while the USB hub re-enumerates: a transient
  missing sysfs sample must not hide loading or failure details.
  """
  if not (delivery or present or active or loading or startup_failed):
    return None
  state = badge_state(compiled, loading, active, startup_failed)
  text = 'eGPU ERROR' if state == 'error' else 'eGPU'
  # Download/installation progress only when nothing is running or failing now.
  if delivery and not (active or loading or startup_failed):
    text, state = delivery
  # Background re-attach after a runtime failure, while the internal model drives.
  if recovery and not active:
    text, state = recovery
  return text, state
