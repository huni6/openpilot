"""ClusterHudDisplayMode: what the external HUD shows. 0 off, 1 the ambient screen as it was, 2 that plus the
comma's current alert; 3-6 are reserved and act as 2 for now."""
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

BUNDLE = Path(__file__).resolve().parents[1]
CARROT = BUNDLE.parent
ROOT = CARROT.parents[1]
if str(BUNDLE) not in sys.path:
  sys.path.insert(0, str(BUNDLE))

from cluster_config import (CLUSTER_DISPLAY_MODE_PARAM, cluster_display_shows_events,
                            normalize_cluster_display_mode)


def test_mode_values():
  assert [normalize_cluster_display_mode(v) for v in (0, 1, 2, 3, 6)] == [0, 1, 2, 3, 6]
  assert [normalize_cluster_display_mode(v) for v in ("0", b"2", " 5 ")] == [0, 2, 5]
  for bad in (None, -1, 7, 99, "x", b"", True, 2.5j):
    assert normalize_cluster_display_mode(bad) == 1          # unreadable or out of range: the default, 1


def test_events_show_from_mode_2():
  assert not cluster_display_shows_events(0)
  assert not cluster_display_shows_events(1)
  assert all(cluster_display_shows_events(m) for m in range(2, 7))
  assert not cluster_display_shows_events(99)                 # an out-of-range value is the default, 1


class FakeParams:
  def __init__(self, values):
    self.values = values

  def get_int(self, key):
    if key not in self.values:
      raise KeyError(key)
    return int(self.values[key])

  def get_bool(self, key):
    return bool(self.values.get(key, False))


@pytest.fixture
def autorun(monkeypatch):
  params_mod = ModuleType("openpilot.common.params")
  params_mod.Params = FakeParams
  monkeypatch.setitem(sys.modules, "openpilot.common.params", params_mod)
  monkeypatch.syspath_prepend(str(ROOT))
  sys.modules.pop("selfdrive.carrot.cluster_autorun", None)
  return importlib.import_module("selfdrive.carrot.cluster_autorun")


def test_mode_0_keeps_the_hud_output_off_even_onroad(autorun):
  on = {"IsOnroad": True, "ClusterHudDebug": 0}
  assert autorun._hud_output_allowed(FakeParams({**on, CLUSTER_DISPLAY_MODE_PARAM: 1}))
  assert autorun._hud_output_allowed(FakeParams({**on, CLUSTER_DISPLAY_MODE_PARAM: 2}))
  assert not autorun._hud_output_allowed(FakeParams({**on, CLUSTER_DISPLAY_MODE_PARAM: 0}))
  assert not autorun._hud_output_allowed(FakeParams({"IsOnroad": False, "ClusterHudDebug": 1, CLUSTER_DISPLAY_MODE_PARAM: 0}))
  assert autorun._hud_output_off_reason(FakeParams({**on, CLUSTER_DISPLAY_MODE_PARAM: 0})) == "ClusterHudDisplayMode=0"


def test_a_missing_mode_counts_as_on(autorun):
  assert autorun._hud_output_allowed(FakeParams({"IsOnroad": True, "ClusterHudDebug": 0}))   # an older params build
  assert not autorun._hud_output_allowed(FakeParams({"IsOnroad": False, "ClusterHudDebug": 0}))


def test_the_setting_is_registered_and_in_carrot_web():
  keys = (ROOT / "common" / "params_keys.h").read_text()
  assert '{"ClusterHudDisplayMode", {PERSISTENT, INT, "1"}},' in keys
  catalog = json.loads((ROOT / "selfdrive" / "carrot_settings.json").read_text())
  entry = next(p for p in catalog["params"] if p["name"] == CLUSTER_DISPLAY_MODE_PARAM)
  assert (entry["min"], entry["max"], entry["default"]) == (0, 6, 1)
  assert all(line in entry["descr"] for line in ("0:", "1:", "2:", "3~6:"))
  sections = [s for c in catalog["menu"] for g in c["groups"] for s in g.get("groups", []) if s.get("id") == "HUD_BASIC"]
  assert sections and CLUSTER_DISPLAY_MODE_PARAM in sections[0]["params"]
