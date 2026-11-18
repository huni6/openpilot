import json
import os
from typing import Any, Dict, List, Tuple

from ..config import DEFAULT_SETTINGS_PATH

try:
  from openpilot.common.vehicle_dynamics import RATIO_KEY, center_ratio_default
except Exception:  # the web runs on its own too (tests, a Mac)
  RATIO_KEY = "VehicleCenterToFrontRatio"
  center_ratio_default = None


# mtime-based cache for carrot_settings.json
# - "path" is mutated by carrot_server.py at startup if --settings is passed
# - "mtime" tracks the last loaded file mtime so reload happens only on change
settings_cache: dict = {
  "path": DEFAULT_SETTINGS_PATH,
  "mtime": 0,
  "data": None,        # full json
  "groups": None,      # {group: [param,...]}
  "by_name": None,     # {name: param}
  "groups_list": None, # [{group, egroup, count}, ...]
  "categories": None,  # 대>중>소 트리 ([{id,ko,en,zh,groups:[...]}]) or None when no "menu"
}


def read_settings_file(path: str) -> Dict[str, Any]:
  with open(path, "r", encoding="utf-8") as f:
    return json.load(f)


def group_index(settings: Dict[str, Any]) -> Tuple[Dict[str, list], Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
  groups: Dict[str, list] = {}
  by_name: Dict[str, Dict[str, Any]] = {}
  groups_list: List[Dict[str, Any]] = []

  params = settings.get("params", [])
  for p in params:
    g = p.get("group", "기타")
    if g == "기타":
        if "egroup" not in p: p["egroup"] = "Other"
        if "cgroup" not in p: p["cgroup"] = "其他"

    groups.setdefault(g, []).append(p)
    n = p.get("name")
    if n:
      by_name[n] = p

  # group list with egroup/cgroup guess
  for g, items in groups.items():
    egroup = None
    cgroup = None
    for it in items:
      if not egroup and it.get("egroup"):
        egroup = it.get("egroup")
      if not cgroup and it.get("cgroup"):
        cgroup = it.get("cgroup")
      if egroup and cgroup:
        break
    groups_list.append({"group": g, "egroup": egroup, "cgroup": cgroup, "count": len(items)})

  return groups, by_name, groups_list


def _label(node: Dict[str, Any]) -> Dict[str, Any]:
  return {"ko": node.get("ko"), "en": node.get("en"), "zh": node.get("zh")}


def build_menu_categories(data: Dict[str, Any], by_name: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]] | None:
  """Build the 대>중>소 tree from the optional top-level "menu" block.

  Returns None when no "menu" is present so the frontend falls back to the
  flat group_index view. Leaf "items" carry param *names* only; the frontend
  resolves definitions from items_by_group to avoid duplicating param data.

  Shape:
    [ {id, ko, en, zh,
       groups: [ {id, ko, en, zh, count,
                  sections: [ {id, ko, en, zh, items:[name,...]} ]} ]} ]

  A 중-group whose menu node holds params directly (no nested "groups") is
  normalized to a single label-less section.
  """
  menu = data.get("menu")
  if not menu:
    return None

  def join_labels(nodes: List[Dict[str, Any]], key: str) -> str | None:
    parts = [str(n.get(key) or "").strip() for n in nodes]
    parts = [p for p in parts if p]
    return " · ".join(parts) if parts else None

  def sections_from(nodes: List[Dict[str, Any]], parents: List[Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    parents = parents or []
    for node in nodes:
      path = parents + [node]
      children = node.get("groups") or []
      if children:
        sections.extend(sections_from(children, path))
        continue
      items = [n for n in node.get("params", []) if n in by_name]
      if not items:
        continue
      sections.append({
        "id": "__".join(str(n.get("id") or "") for n in path if n.get("id")),
        "ko": join_labels(path, "ko"),
        "en": join_labels(path, "en"),
        "zh": join_labels(path, "zh"),
        "items": items,
      })
    return sections

  cats: List[Dict[str, Any]] = []
  for cat in menu:
    groups_out: List[Dict[str, Any]] = []
    for grp in cat.get("groups", []):
      if "groups" in grp:
        sections = sections_from(grp["groups"])
      else:
        # params directly under the 중-group → single label-less section
        sections = [{"id": grp.get("id"), "ko": None, "en": None, "zh": None,
                     "items": [n for n in grp.get("params", []) if n in by_name]}]
      count = sum(len(s["items"]) for s in sections)
      groups_out.append({**_label(grp), "id": grp.get("id"), "count": count, "sections": sections})
    cats.append({**_label(cat), "id": cat.get("id"), "groups": groups_out})
  return cats


def current_center_ratio_default(params=None) -> float | None:
  """The installed vehicle's VehicleCenterToFrontRatio from CarParamsPersistent, or None before the car is known."""
  if center_ratio_default is None:
    return None
  if params is None:
    from .params import HAS_PARAMS, Params
    if not HAS_PARAMS or Params is None:
      return None
    params = Params()
  try:
    raw = params.get("CarParamsPersistent")
    if raw:
      from cereal import car
      with car.CarParams.from_bytes(raw) as cp:
        return center_ratio_default(cp)
  except Exception:
    pass
  return None


def with_center_ratio_default(cache_parts: tuple, default: float | None) -> tuple:
  """VehicleCenterToFrontRatio shows the installed vehicle's ratio as its default (the web's default button), and is
  left out until the car is known rather than showing a made-up default (as WIP-FR does)."""
  data, groups, by_name, groups_list = cache_parts
  if RATIO_KEY not in by_name:
    return cache_parts
  if default is None:
    def adapted(items):
      return [item for item in items if item.get("name") != RATIO_KEY]
    by_name = {name: item for name, item in by_name.items() if name != RATIO_KEY}
  else:
    setting = {**by_name[RATIO_KEY], "default": default}

    def adapted(items):
      return [setting if item.get("name") == RATIO_KEY else item for item in items]
    by_name = {**by_name, RATIO_KEY: setting}
  groups = {group: adapted(items) for group, items in groups.items()}
  groups_list = [{**entry, "count": len(groups.get(entry.get("group"), []))} for entry in groups_list]
  return {**data, "params": adapted(data.get("params", []))}, groups, by_name, groups_list


def get_settings_view() -> tuple[dict[str, Any], dict[str, list], dict[str, dict[str, Any]], list[dict[str, Any]], Any]:
  """get_settings_cached() plus the menu tree, both adapted to the current vehicle."""
  data, groups, by_name, groups_list = get_settings_cached()
  categories = settings_cache.get("categories")
  if categories is not None and RATIO_KEY not in by_name:
    categories = build_menu_categories(data, by_name)
  return data, groups, by_name, groups_list, categories


def get_settings_cached() -> Tuple[Dict[str, Any], Dict[str, list], Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
  path = settings_cache["path"]
  st = os.stat(path)
  mtime = int(st.st_mtime)
  if settings_cache["data"] is None or settings_cache["mtime"] != mtime:
    data = read_settings_file(path)
    groups, by_name, groups_list = group_index(data)
    settings_cache.update({
      "mtime": mtime,
      "data": data,
      "groups": groups,
      "by_name": by_name,
      "groups_list": groups_list,
      "categories": build_menu_categories(data, by_name),
    })
  return with_center_ratio_default((
    settings_cache["data"],
    settings_cache["groups"],
    settings_cache["by_name"],
    settings_cache["groups_list"],
  ), current_center_ratio_default())
