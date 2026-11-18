"""VehicleCenterToFrontRatio in Carrot Web: the installed vehicle's ratio as the default, hidden until the car is known,
stored 0 when it is the vehicle's own, validated (not clamped) on save, shown as the effective value."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from cereal import car

from openpilot.selfdrive.carrot.server.services import params as sparams
from openpilot.selfdrive.carrot.server.services import settings as ssettings
from openpilot.selfdrive.carrot.server.features import params as fparams

CATALOG = Path(__file__).resolve().parents[3] / "carrot_settings.json"


def cp_bytes(ratio=0.5):
  return car.CarParams.new_message(wheelbase=2.9, centerToFront=2.9 * ratio, mass=2200).to_bytes()


class FakeParams:
  def __init__(self, values):
    self.values = values

  def get(self, key, block=False, return_default=False):
    return self.values.get(key)


def test_catalog_entry():
  d = json.loads(CATALOG.read_text())
  p = next(p for p in d["params"] if p["name"] == "VehicleCenterToFrontRatio")
  assert (p["min"], p["max"], p["unit"], p["default"], p["vehicle_default"]) == (0.1, 0.9, 0.01, None, True)
  steer = next(g for c in d["menu"] for g in c["groups"] if g["id"] == "STEER")
  assert ["VehicleCenterToFrontRatio"] in [g.get("params") for g in steer["groups"]]


def test_vehicle_default_from_car_params():
  assert ssettings.current_center_ratio_default(FakeParams({"CarParamsPersistent": cp_bytes(0.5)})) == pytest.approx(0.5)
  assert ssettings.current_center_ratio_default(FakeParams({})) is None
  assert ssettings.current_center_ratio_default(FakeParams({"CarParamsPersistent": b"junk"})) is None


def parts():
  data = json.loads(CATALOG.read_text())
  groups, by_name, groups_list = ssettings.group_index(data)
  return data, groups, by_name, groups_list


def test_listing_shows_the_vehicle_default_or_hides_the_entry():
  data, groups, by_name, groups_list = ssettings.with_center_ratio_default(parts(), 0.5)
  assert by_name["VehicleCenterToFrontRatio"]["default"] == 0.5
  assert next(p for p in data["params"] if p["name"] == "VehicleCenterToFrontRatio")["default"] == 0.5
  data, groups, by_name, groups_list = ssettings.with_center_ratio_default(parts(), None)
  assert "VehicleCenterToFrontRatio" not in by_name
  assert all(p["name"] != "VehicleCenterToFrontRatio" for items in groups.values() for p in items)
  cats = ssettings.build_menu_categories(data, by_name)
  names = [n for c in cats for g in c["groups"] for s in g["sections"] for n in s["items"]]
  assert "VehicleCenterToFrontRatio" not in names and "ClampEnable" in names and "LanelessCurvature" in names
  count = {g["group"]: g["count"] for g in groups_list}
  assert count["차량 제원"] == 0


def test_shown_value_is_the_effective_ratio(monkeypatch):
  monkeypatch.setattr(ssettings, "current_center_ratio_default", lambda params=None: 0.5)
  assert sparams._read_param_value(FakeParams({"VehicleCenterToFrontRatio": 0.0}), "VehicleCenterToFrontRatio", None) == 0.5
  assert sparams._read_param_value(FakeParams({}), "VehicleCenterToFrontRatio", None) == 0.5
  assert sparams._read_param_value(FakeParams({"VehicleCenterToFrontRatio": 0.47}), "VehicleCenterToFrontRatio", None) == 0.47
  assert sparams._read_param_value(FakeParams({"VehicleCenterToFrontRatio": 3.0}), "VehicleCenterToFrontRatio", None) == 0.5


def test_saving_stores_zero_for_the_vehicles_own_ratio_and_rejects_bad_values(monkeypatch):
  written = {}
  monkeypatch.setattr(sparams, "HAS_PARAMS", True)
  monkeypatch.setattr(sparams, "Params", lambda: NS())
  monkeypatch.setattr(sparams, "put_typed", lambda params, key, value: written.__setitem__(key, value))
  monkeypatch.setattr(ssettings, "current_center_ratio_default", lambda params=None: 0.5)
  sparams.set_param_value("VehicleCenterToFrontRatio", 0.5)
  assert written["VehicleCenterToFrontRatio"] == 0.0
  sparams.set_param_value("VehicleCenterToFrontRatio", "0.55")
  assert written["VehicleCenterToFrontRatio"] == pytest.approx(0.55)
  for bad in (70, 0, 0.95, "x", True):
    with pytest.raises(ValueError):
      sparams.set_param_value("VehicleCenterToFrontRatio", bad)
  sparams.set_param_value("LatJerkMax", 60)   # other settings untouched
  assert written["LatJerkMax"] == 60


def test_api_rejects_a_percentage_instead_of_clamping_it(monkeypatch):
  calls = []
  monkeypatch.setitem(ssettings.settings_cache, "path", str(CATALOG))
  monkeypatch.setitem(ssettings.settings_cache, "data", None)
  def set_param_value(name, value):
    calls.append((name, value))
    if name == "VehicleCenterToFrontRatio":
      sparams.center_ratio_to_store(value, 0.5)
  monkeypatch.setattr(fparams, "set_param_value", set_param_value)

  class Req:
    def __init__(self, body):
      self.body = body

    async def json(self):
      return self.body
  res = asyncio.run(fparams.api_param_set(Req({"name": "VehicleCenterToFrontRatio", "value": 70})))
  assert res.status == 400 and calls == [("VehicleCenterToFrontRatio", 70)]   # passed through unclamped, then refused
  res = asyncio.run(fparams.api_param_set(Req({"name": "VehicleCenterToFrontRatio", "value": 0.52})))
  assert res.status == 200
  res = asyncio.run(fparams.api_param_set(Req({"name": "LatJerkMax", "value": 500})))
  assert res.status == 200 and calls[-1] == ("LatJerkMax", 100)                # others are still clamped


def test_restore_profile_and_reset_store_the_vehicles_ratio_as_zero():
  assert sparams.center_ratio_restore_value(0.5, 0.5) == 0.0       # a profile or reset with the vehicle's ratio
  assert sparams.center_ratio_restore_value("0", 0.5) == 0.0       # a backup of 'follow the vehicle'
  assert sparams.center_ratio_restore_value(0.0, None) == 0.0
  assert sparams.center_ratio_restore_value("0.45", 0.5) == pytest.approx(0.45)
  for bad in (0.95, "x", True, None):
    with pytest.raises(ValueError):
      sparams.center_ratio_restore_value(bad, 0.5)


def test_restore_preview_compares_what_the_page_shows(monkeypatch):
  class TypedParams(FakeParams):
    def get_type(self, key):
      return sparams.ParamKeyType.FLOAT if key == "VehicleCenterToFrontRatio" else sparams.ParamKeyType.INT

  if sparams.ParamKeyType is None:
    import enum

    class PKT(enum.IntEnum):
      BOOL = 1
      INT = 2
      FLOAT = 3
      TIME = 4
      STRING = 0
      JSON = 5
      BYTES = 6
    monkeypatch.setattr(sparams, "ParamKeyType", PKT)
  monkeypatch.setattr(sparams, "HAS_PARAMS", True)
  stored = {"VehicleCenterToFrontRatio": 0.0}
  monkeypatch.setattr(sparams, "Params", lambda: TypedParams(stored))
  monkeypatch.setattr(ssettings, "current_center_ratio_default", lambda params=None: 0.5)
  monkeypatch.setattr(sparams, "get_param_values", lambda names, defaults: {
    n: sparams._read_param_value(TypedParams(stored), n, None) for n in names})
  entry = sparams.preview_param_restore_values({"VehicleCenterToFrontRatio": 0.5})["entries"][0]
  assert entry["status"] == "same" and not entry["apply"]          # following the vehicle already
  stored["VehicleCenterToFrontRatio"] = 0.45
  entry = sparams.preview_param_restore_values({"VehicleCenterToFrontRatio": 0.5})["entries"][0]
  assert entry["apply"] and entry["value"] == 0.0                  # back to following the vehicle, stored as 0
  entry = sparams.preview_param_restore_values({"VehicleCenterToFrontRatio": 0.7})["entries"][0]
  assert entry["apply"] and entry["value"] == pytest.approx(0.7)
  entry = sparams.preview_param_restore_values({"VehicleCenterToFrontRatio": 1.5})["entries"][0]
  assert entry["status"] == "invalid" and not entry["apply"]
