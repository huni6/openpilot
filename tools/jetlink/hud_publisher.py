"""Comma-side low-priority producer using the original live data mapping."""
import os
import logging
from pathlib import Path
import sys
import time

from hud_protocol import ROOT, HEADER, PARAM_DEFAULTS, atomic_write, encode_snapshot

sys.path.insert(0, str(ROOT))


def main():
  import ctypes
  import signal
  parent = int(sys.argv[2])
  if ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != parent:
    raise RuntimeError('Cluster snapshot producer parent unavailable')
  from openpilot.common.params import Params
  from cluster_live import OpenpilotLiveSource
  os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
  os.nice(10)
  path = Path(sys.argv[1])
  params = Params()
  source = OpenpilotLiveSource(include_can=False)
  last_art = None
  next_log = 0.
  try:
    while True:
      started = time.monotonic()
      try:
        settings = {k: int(params.get_bool(k)) if k == 'IsOnroad' else params.get_int(k) for k in PARAM_DEFAULTS}
        state = source.update()
        payload, art = encode_snapshot(state, settings, time.time())  # noqa: TID251 -- transport the comma calendar clock
        if art != last_art:
          atomic_write(path / 'art', art)
          last_art = art
        atomic_write(path / 'snapshot', HEADER.pack(started) + payload)
      except Exception:
        if started >= next_log:
          logging.exception('Cluster snapshot unavailable')
          next_log = started + 5
      time.sleep(max(0, .1 - (time.monotonic() - started)))
  finally:
    source.close()


if __name__ == '__main__':
  logging.basicConfig(level=logging.INFO)
  main()
