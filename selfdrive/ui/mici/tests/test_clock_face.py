"""NewUi=2 clock face: geometry against the reference design, hand angles, night switching, draw calls.

Ported from WIP-FR 5325a288 (the sun height now lives in clock_face.py; a bool NewUi is the Carrot UI here)."""
import ast
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS
import sys

import pytest

ONROAD = Path(__file__).parents[1] / 'onroad'
REF_W, REF_H = 577, 276  # reference screenshot face size


class SM(dict):
  def __init__(self):
    super().__init__(carState=NS(vEgo=62 / 3.6, vEgoCluster=0.0), carControl=NS(latActive=False))
    self.alive = {'carState': True, 'carControl': True}
    self.recv_frame = {'carState': 11, 'carControl': 11}


@pytest.fixture
def clock(monkeypatch):
  calls = []
  ui = NS(started_frame=10, is_metric=True, sm=SM())
  font = NS(recs=[NS(x=0, y=0, width=10, height=14)], glyphs=[NS(offsetX=0, offsetY=3, advanceX=11)], texture='font')
  rl = NS(Color=lambda *c: tuple(c), Vector2=lambda x, y: (x, y), Rectangle=lambda *r: tuple(r), BLACK=(0, 0, 0, 255),
          get_glyph_index=lambda _font, _cp: 0)
  for name in ('draw_line_ex', 'draw_circle_v', 'draw_texture_pro', 'draw_rectangle_rec'):
    setattr(rl, name, lambda *args, name=name: calls.append((name, *args)))
  gui_app = NS(font=lambda *_: font, target_fps=20, texture=lambda path, w, h: NS(width=w, height=h, path=path))
  monkeypatch.setitem(sys.modules, 'pyray', rl)
  monkeypatch.setitem(sys.modules, 'openpilot.selfdrive.ui.ui_state', NS(ui_state=ui))
  monkeypatch.setitem(sys.modules, 'openpilot.system.ui.lib.application', NS(gui_app=gui_app, FontWeight=NS(BOLD='bold')))
  spec = importlib.util.spec_from_file_location('_test_clock_face', ONROAD / 'clock_face.py')
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return NS(m=module, ui=ui, calls=calls)


def norm(x, y):
  return round(x / REF_W, 3), round(y / REF_H, 3)


def test_marks_follow_the_reference_design(clock):
  marks = clock.m.dial_marks(REF_W, REF_H)
  assert len(marks) == 60
  assert sum(kind == 'hour' for *_, kind in marks) == 12
  # Values measured from the reference screenshot (x of width, y of height, centre origin).
  tol = 0.006
  def close(point, expected):
    return all(abs(a - b) <= tol for a, b in zip(norm(*point), expected, strict=True))
  two = marks[10]   # 2 o'clock: long line between the inner and outer boxes
  assert close(two[:2], (0.194, -0.234)) and close(two[2:4], (0.342, -0.414))
  one = marks[5]
  assert close(one[:2], (0.064, -0.234)) and close(one[2:4], (0.114, -0.414))
  three = marks[15]
  assert close(three[:2], (0.349, 0.0)) and close(three[2:4], (0.432, 0.0))
  twelve = marks[0]  # only the short outer tick above the numeral
  assert close(twelve[:2], (0.0, -0.359)) and close(twelve[2:4], (0.0, -0.414))
  assert close(marks[7][2:4], (0.178, -0.414)) and close(marks[9][2:4], (0.272, -0.414))
  assert close(marks[11][2:4], (0.432, -0.402))  # past the corner, the ray stops on the side
  for minute in (1, 14, 29, 44, 59):
    x0, y0, x1, y1, kind = marks[minute]
    assert kind == 'minute'
    assert max(abs(x1) / REF_W / 0.432, abs(y1) / REF_H / 0.414) == pytest.approx(1.0)
    assert max(abs(x0) / REF_W / 0.405, abs(y0) / REF_H / 0.359) == pytest.approx(1.0)


def test_marks_are_symmetric(clock):
  marks = clock.m.dial_marks(536, 240)
  for minute in range(1, 30):
    a, b = marks[minute], marks[60 - minute]
    assert a[0] == pytest.approx(-b[0]) and a[1] == pytest.approx(b[1]) and a[4] == b[4]


def test_hand_angles_sweep(clock):
  hour, minute, second = clock.m.hand_angles(datetime(2026, 11, 17, 22, 10, 42), 0.3)
  assert second == pytest.approx(42.3 * 6)
  assert minute == pytest.approx((10 + 42.3 / 60) * 6)
  assert hour == pytest.approx((10 + (10 + 42.3 / 60) / 60) * 30)
  assert clock.m.hand_angles(datetime(2026, 1, 1, 12, 0, 0), 0.0) == (0.0, 0.0, 0.0)


def test_kst_is_fixed_offset(clock):
  assert clock.m.kst_now(0).hour == 9


WALL = 1_763_000_000.0  # a set comma clock (2025-11)


def test_night_mode_follows_the_sun_with_hysteresis(clock):
  height = [10.0]
  mode = clock.m.NightMode(sun=lambda wall: height[0])
  assert mode.update(0.0) is False and not mode.known        # clock not set yet: day, undecided
  height[0] = -3.6
  assert mode.update(WALL) is True and mode.known            # first decision uses the midpoint (-3.5)
  mode.reset()
  height[0] = -3.4
  assert mode.update(WALL) is False
  for h, night in ((-3.9, False), (-4.0, True), (-3.5, True), (-3.0, True), (-2.9, False), (-3.9, False)):
    height[0] = h
    assert mode.update(WALL) is night, h
  height[0] = -10.0
  assert mode.update(WALL) is True
  assert mode.update(float('nan')) is True                   # bad clock keeps the last decision
  height[0] = float('nan')
  assert mode.update(WALL) is True


def test_night_mode_real_sun_in_seoul(clock):
  mode = clock.m.NightMode()
  def kst(day, hour, minute):
    return datetime(2026, 11, day, hour, minute).timestamp() - clock.m.KST_OFFSET_SECONDS - \
      (datetime(2026, 1, 1).timestamp() - datetime(2026, 1, 1, tzinfo=UTC).timestamp())
  assert mode.update(kst(17, 12, 0)) is False
  assert mode.update(kst(17, 16, 30)) is False
  assert mode.update(kst(17, 18, 0)) is True                 # sunset ~17:20, -4 deg ~17:40
  assert mode.update(kst(18, 6, 0)) is True
  assert mode.update(kst(18, 7, 30)) is False                # sunrise ~7:15


def lines(calls):
  return [c for c in calls if c[0] == 'draw_line_ex']


def test_day_face_draws_second_hand_and_orange_hub(clock):
  face = clock.m.ClockFace()
  face.render(NS(x=0, y=0, width=536, height=240), timestamp=0.0, night=False)
  drawn = lines(clock.calls)
  assert len(drawn) == 60 + 2 * 2 + 1                 # marks, two hands (stem + bar), second hand
  assert drawn[-1][-1] == (255, 159, 10, 255)
  hub = [c for c in clock.calls if c[0] == 'draw_circle_v'][-2]
  assert hub[-1] == (255, 159, 10, 255)
  assert clock.calls[0][0] == 'draw_rectangle_rec'    # opaque black background first


def test_night_face_is_red_without_second_hand(clock):
  face = clock.m.ClockFace()
  face.render(NS(x=0, y=0, width=536, height=240), timestamp=0.0, night=True)
  drawn = lines(clock.calls)
  assert len(drawn) == 60 + 2 * 2
  colors = {c[-1] for c in drawn} | {c[-1] for c in clock.calls if c[0] == 'draw_texture_pro'}
  assert all(r > 100 and g < 60 and b < 60 for r, g, b, _ in colors)


def test_first_frame_of_a_drive_shows_night_without_fading(clock):
  face = clock.m.ClockFace()
  face._night = clock.m.NightMode(sun=lambda wall: -20.0)
  face.render(NS(x=0, y=0, width=536, height=240), timestamp=WALL)
  assert len(lines(clock.calls)) == 60 + 2 * 2


def test_sunset_fades_instead_of_snapping(clock):
  height = [5.0]
  face = clock.m.ClockFace()
  face._night = clock.m.NightMode(sun=lambda wall: height[0])
  rect = NS(x=0, y=0, width=536, height=240)
  face.render(rect, timestamp=WALL)
  assert face._blend.x == 0.0
  height[0] = -5.0
  face.render(rect, timestamp=WALL)
  assert 0.0 < face._blend.x < 0.2
  for _ in range(40):
    face.render(rect, timestamp=WALL)
  assert face._blend.x > 0.9


def test_wheel_icon_color_follows_lateral_control(clock):
  face = clock.m.ClockFace()
  rect = NS(x=0, y=0, width=536, height=240)
  face.render(rect, timestamp=0.0, night=False)
  wheel = next(c for c in clock.calls if c[0] == 'draw_texture_pro' and c[1] != 'font')
  assert wheel[-1] == (150, 150, 150, 255)
  clock.calls.clear()
  clock.ui.sm['carControl'].latActive = True
  face.render(rect, timestamp=0.0, night=False)
  wheel = next(c for c in clock.calls if c[0] == 'draw_texture_pro' and c[1] != 'font')
  assert wheel[-1] == (0, 255, 0, 255)
  clock.ui.sm.recv_frame['carControl'] = 9      # previous drive: not steering now
  assert face._lateral_active() is False


def test_speed_uses_cluster_speed_once_seen_and_hides_stale_state(clock):
  face = clock.m.ClockFace()
  assert face._speed_text() == '62'
  clock.ui.sm['carState'].vEgoCluster = 63 / 3.6
  assert face._speed_text() == '63'
  clock.ui.sm['carState'].vEgoCluster = 0.0
  assert face._speed_text() == '0'                # stays on the cluster speed at a stop
  clock.ui.sm.alive['carState'] = False
  assert face._speed_text() == '--'
  clock.ui.sm.alive['carState'] = True
  clock.ui.is_metric = False
  clock.ui.sm['carState'].vEgoCluster = 100 / 3.6
  assert face._speed_text() == '62'


def new_ui_mode():
  source = Path(__file__).parents[2] / 'ui_state.py'
  tree = ast.parse(source.read_text())
  keep = [n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '').startswith('NEW_UI_')]
  keep += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'new_ui_mode']
  namespace = {}
  exec(compile(ast.Module(body=keep, type_ignores=[]), str(source), 'exec'), namespace)
  return namespace['new_ui_mode']


@pytest.mark.parametrize('stored,mode', [(None, 0), (0, 0), (1, 1), (2, 2), (3, 3), (4, 0), (-1, 0), (True, 0), (False, 0), ('2', 0),
                                         (2.0, 0)])
def test_new_ui_mode_parsing(stored, mode):
  assert new_ui_mode()(stored) == mode


def test_sun_height_matches_known_seoul_values(clock):
  # NOAA approximation (within about 0.5 deg): solar noon on the winter and summer solstices, and midnight
  def utc(*a):
    return datetime(*a, tzinfo=UTC).timestamp()
  assert clock.m.sun_elevation_deg(utc(2026, 12, 21, 3, 30)) == pytest.approx(90 - 37.5 - 23.44, abs=1.0)
  assert clock.m.sun_elevation_deg(utc(2026, 6, 21, 3, 30)) == pytest.approx(90 - 37.5 + 23.44, abs=1.0)
  assert clock.m.sun_elevation_deg(utc(2026, 6, 21, 15, 30)) < -20
