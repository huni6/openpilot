#!/usr/bin/env python3
"""Boot-time install check and smoke test for the selected precompiled eGPU model.

Ported from the precompiled branch of carrot-wip's system/manager/build.py
(build_usbgpu_model). This branch has no local big-model compiler path for the
catalog models: they are all precompiled PKL packages, so a failure here only
leaves the internal model in use for this boot.
"""
import subprocess
import sys

from openpilot.common.basedir import BASEDIR


def prepare_for_startup(spinner) -> bool:
  from openpilot.selfdrive.modeld.big_model import selected_manifest, model_cache_dir
  from openpilot.selfdrive.modeld.big_model_status import write_big_model_status
  from openpilot.selfdrive.modeld.helpers import usbgpu_present
  from openpilot.selfdrive.modeld.precompiled_model import ensure_precompiled, installed, record_failure
  from openpilot.selfdrive.modeld.precompiled_validation import (camera_sizes, device_context, validation_key,
                                                                 validation_cached, save_validation)

  manifest = selected_manifest()
  if manifest is None:
    return False
  status_values = {
    "model_id": manifest.model_id,
    "sha256": manifest.sha256,
    "downloaded_bytes": manifest.size,
    "total_bytes": manifest.size,
  }
  if not manifest.precompiled_only:
    write_big_model_status(model_cache_dir(), "error", detail="only precompiled eGPU models are supported on this branch", **status_values)
    return False

  try:
    spinner.update("USB eGPU big model\nChecking precompiled model")

    def download_progress(done, total):
      spinner.update(f"USB eGPU big model\nDownloading precompiled model {done * 100 // total}%")
      write_big_model_status(model_cache_dir(), "downloading", model_id=manifest.model_id, sha256=manifest.sha256,
                             downloaded_bytes=done, total_bytes=total, detail="precompiled model")
    # big_model --prepare-for-startup has just installed and verified the package;
    # the worker re-hashes the PKL on every load, so avoid a third full hash here.
    precompiled = installed(manifest) or ensure_precompiled(manifest, progress=download_progress)
  except Exception as exc:
    print(f"Precompiled eGPU model unavailable; using internal model: {exc}")
    precompiled = None
  if precompiled is None:
    write_big_model_status(model_cache_dir(), "error", detail="precompiled model unavailable; using internal model", **status_values)
    return False

  try:
    if usbgpu_present():
      sizes, key = camera_sizes(''), None
      try:
        device = device_context()
        sizes = camera_sizes(device['device'])
        key = validation_key(precompiled, device, sizes)
      except (OSError, ValueError, KeyError, TypeError, ImportError):
        print('Precompiled validation identity unavailable; running smoke test without cache')
      if validation_cached(precompiled, key):
        print('Reusing successful precompiled eGPU validation for this device/runtime')
      else:
        spinner.update("USB eGPU big model\nValidating precompiled model")
        command = [sys.executable, '-m', 'openpilot.selfdrive.modeld.precompiled_runner', str(precompiled)]
        for width, height in sizes:
          command += ['--camera', f'{width}x{height}']
        validation = subprocess.run(command, cwd=BASEDIR, check=True, timeout=300, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(validation.stdout, end='')
        try:
          save_validation(precompiled, key)
        except OSError as exc:
          print(f'Could not cache successful precompiled validation: {exc}')
    write_big_model_status(model_cache_dir(), "compiled", detail="downloaded precompiled model", **status_values)
    print(f"Using precompiled eGPU model: {precompiled}")
    return True
  except Exception as exc:
    output = getattr(exc, 'output', '') or ''
    if isinstance(output, bytes):
      output = output.decode('utf-8', 'replace')
    if output:
      print(output, end='')
    failure = TimeoutError(output or str(exc)) if isinstance(exc, subprocess.TimeoutExpired) else output or exc
    if not record_failure(precompiled, failure, 'boot_validation'):
      # USB enumeration can succeed with ignition off while the GPU's 12V
      # supply is absent. Keep the verified artifact for the next model start.
      detail = 'precompiled model verified; waiting for eGPU readiness or validation retry'
      write_big_model_status(model_cache_dir(), 'waiting_for_ignition', detail=detail, **status_values)
      print(f'Precompiled eGPU validation deferred: {detail}')
      return True
    print(f"Precompiled eGPU validation failed; using internal model: {exc}")
    write_big_model_status(model_cache_dir(), "error", error_code="rejected",
                           detail="startup validation failed; using internal model", **status_values)
    return False


def main() -> int:
  from openpilot.common.spinner import Spinner
  with Spinner() as spinner:
    return 0 if prepare_for_startup(spinner) else 1


if __name__ == "__main__":
  raise SystemExit(main())
