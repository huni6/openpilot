"""Wide analog clock that replaces the C4 onroad view when NewUi is 2.

Display only: alerts are still drawn on top by AugmentedRoadView, and nothing
here reads or changes driving control.

Geometry is expressed as fractions of the face (x of width, y of height),
measured from the requested reference design: 60 radial marks at 6 degree
steps that stop on a box, long hour lines, 12/3/6/9 numerals, a speed
complication on the left and the date on the right. After dark the whole
face turns red and the second hand is hidden.

Darkness follows the sun, not the camera: on the Traverse the road camera's
light estimate (100 - exposure %) reads 81-86 at night and also about 87 in
some daytime driving, so it cannot tell the two apart. The sun height for
Seoul is the rule WIP-FR's Navdy ambient light uses (selfdrive/navdy/ambient_dark.py
there); this branch has no Navdy bridge, so the few lines it needs live here.

Ported from leehyuk1108/carrotpilot WIP-FR (5325a288).
"""
import math
import time
from datetime import UTC, datetime

import pyray as rl

from openpilot.common.constants import CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight

KST_OFFSET_SECONDS = 9 * 60 * 60

# Night: the sun at least SUN_DOWN_DEG below the horizon in Seoul (within about 1.5 degrees anywhere in Korea),
# day again once it is above SUN_UP_DEG. A wall clock before MIN_VALID_WALL is not set yet.
SUN_DOWN_DEG = -4.0
SUN_UP_DEG = -3.0
SEOUL_LAT_LON = (37.5, 127.0)
MIN_VALID_WALL = 1_700_000_000.0  # 2023-11-14


def sun_elevation_deg(wall: float, lat: float = SEOUL_LAT_LON[0], lon: float = SEOUL_LAT_LON[1]) -> float:
  """Sun height above the horizon in degrees at Unix time wall (NOAA approximation, within about 0.5 deg)."""
  t = datetime.fromtimestamp(wall, UTC)
  hours = t.hour + t.minute / 60.0 + t.second / 3600.0
  days = 366.0 if t.year % 4 == 0 and (t.year % 100 != 0 or t.year % 400 == 0) else 365.0   # NOAA: 366 in leap years
  g = 2.0 * math.pi / days * (t.timetuple().tm_yday - 1 + (hours - 12.0) / 24.0)
  declination = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
                 + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
  equation_min = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                           - 0.040849 * math.sin(2 * g))
  hour_angle = math.radians((hours * 60.0 + equation_min + 4.0 * lon) / 4.0 - 180.0)
  latitude = math.radians(lat)
  return math.degrees(math.asin(math.sin(latitude) * math.sin(declination)
                                + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle)))

# Half extents of the boxes the radial marks run between.
OUTER = (0.432, 0.414)
MINUTE_INNER = (0.405, 0.359)
HOUR_INNER = (0.349, 0.234)

NUMERALS = ((12, 0.0, -0.275), (3, 0.305, 0.0), (6, 0.0, 0.272), (9, -0.305, 0.0))
NUMERAL_CAP = 0.087     # digit height / face height
NUMERAL_ASPECT = 1.1    # digit width / digit height (wide face)

HOUR_LINE_W = 0.010
MINUTE_TICK_W = 0.006

HOUR_HAND = 0.225
MINUTE_HAND = 0.382
SECOND_HAND = 0.415
HAND_W = 0.030
STEM_W = 0.012
STEM_LEN = 0.058
SECOND_W = 0.007
HUB_R = 0.021
HUB_HOLE_R = 0.009

# The reference complications are 0.036H tall; 1.5x keeps them legible on the
# 240 px C4 panel at windshield distance.
COMPLICATION_CAP = 0.036 * 1.5
LEFT_COMPLICATION_X = -0.186
RIGHT_COMPLICATION_X = 0.200

WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")

DAY = {
  "numeral": (250, 250, 250), "hour": (200, 200, 200), "minute": (100, 100, 100),
  "hand": (250, 250, 250), "second": (255, 159, 10), "hub": (255, 159, 10),
  "text": (245, 245, 245), "weekday": (255, 69, 58), "lat_on": (0, 255, 0), "lat_off": (150, 150, 150),
}
NIGHT = {
  "numeral": (235, 50, 38), "hour": (210, 44, 33), "minute": (140, 30, 24),
  "hand": (235, 50, 38), "second": (235, 50, 38), "hub": (235, 50, 38),
  "text": (235, 50, 38), "weekday": (235, 50, 38), "lat_on": (255, 72, 52), "lat_off": (110, 26, 20),
}


def kst_now(timestamp: float) -> datetime:
  return datetime.fromtimestamp(timestamp + KST_OFFSET_SECONDS, UTC).replace(tzinfo=None)


def ray_to_box(angle_deg: float, half_w: float, half_h: float) -> tuple[float, float]:
  """Point where a ray from the centre at a clock angle (0 = 12 o'clock, clockwise) meets a box."""
  a = math.radians(angle_deg)
  dx, dy = math.sin(a), -math.cos(a)
  tx = half_w / abs(dx) if abs(dx) > 1e-9 else math.inf
  ty = half_h / abs(dy) if abs(dy) > 1e-9 else math.inf
  t = min(tx, ty)
  return dx * t, dy * t


def dial_marks(width: float, height: float) -> list[tuple[float, float, float, float, str]]:
  """Sixty radial marks as (x0, y0, x1, y1, kind), relative to the face centre."""
  marks = []
  outer = (OUTER[0] * width, OUTER[1] * height)
  minute_inner = (MINUTE_INNER[0] * width, MINUTE_INNER[1] * height)
  hour_inner = (HOUR_INNER[0] * width, HOUR_INNER[1] * height)
  for minute in range(60):
    angle = minute * 6.0
    if minute % 5:
      inner, kind = minute_inner, "minute"
    elif minute in (0, 30):
      # The 12 and 6 numerals sit in the hour band, so only the outer tick remains.
      inner, kind = minute_inner, "hour"
    else:
      inner, kind = hour_inner, "hour"
    x0, y0 = ray_to_box(angle, *inner)
    x1, y1 = ray_to_box(angle, *outer)
    marks.append((x0, y0, x1, y1, kind))
  return marks


def hand_angles(local: datetime, fraction: float) -> tuple[float, float, float]:
  """Hour, minute and sweeping second hand angles in clock degrees."""
  seconds = local.second + fraction
  minutes = local.minute + seconds / 60.0
  hours = (local.hour % 12) + minutes / 60.0
  return hours * 30.0, minutes * 6.0, seconds * 6.0


def mix(day: tuple[int, int, int], night: tuple[int, int, int], t: float, alpha: float = 1.0) -> rl.Color:
  t = min(max(t, 0.0), 1.0)
  return rl.Color(*(int(round(d + (n - d) * t)) for d, n in zip(day, night, strict=True)), int(round(255 * alpha)))


class NightMode:
  """Night after the sun is SUN_DOWN_DEG below the horizon (Seoul), day again above SUN_UP_DEG."""

  def __init__(self, sun=sun_elevation_deg):
    self._sun = sun
    self.night = False
    self._known = False

  @property
  def known(self) -> bool:
    return self._known

  def reset(self) -> None:
    self.night = False
    self._known = False

  def update(self, wall: float) -> bool:
    if not math.isfinite(wall) or wall < MIN_VALID_WALL:
      # The comma clock is not set yet: keep the last decision (day before the first one).
      return self.night
    try:
      elevation = float(self._sun(wall))
    except (ValueError, OverflowError):
      return self.night
    if not math.isfinite(elevation):
      return self.night
    if not self._known:
      self.night = elevation <= (SUN_DOWN_DEG + SUN_UP_DEG) / 2
      self._known = True
    elif self.night:
      self.night = elevation <= SUN_UP_DEG
    else:
      self.night = elevation <= SUN_DOWN_DEG
    return self.night


class ClockFace:
  def __init__(self):
    self._font = gui_app.font(FontWeight.BOLD)
    self._night = NightMode()
    self._blend = FirstOrderFilter(0.0, 0.7, 1 / gui_app.target_fps)
    self._started_frame = -1
    self._snap_blend = True
    self._cluster_speed_seen = False
    self._wheel_size = 0
    self._wheel = None

  # ---- text helpers -------------------------------------------------------
  def _glyph(self, char: str):
    index = rl.get_glyph_index(self._font, ord(char))
    return self._font.recs[index], self._font.glyphs[index]

  def _scale_for_cap(self, cap_px: float) -> float:
    rec, _ = self._glyph("0")
    return cap_px / max(rec.height, 1.0)

  def _text_width(self, text: str, scale: float, stretch: float) -> float:
    width = 0.0
    for char in text:
      rec, glyph = self._glyph(char)
      width += (glyph.advanceX or rec.width) * scale * stretch
    return width

  def _draw_text(self, text: str, x: float, center_y: float, scale: float, stretch: float, color: rl.Color) -> float:
    """Draw text with its digit cap box centred on center_y; returns the advance."""
    zero_rec, zero_glyph = self._glyph("0")
    top = center_y - (zero_glyph.offsetY + zero_rec.height / 2) * scale
    start = x
    for char in text:
      rec, glyph = self._glyph(char)
      if rec.width > 0 and rec.height > 0:
        dest = rl.Rectangle(x + glyph.offsetX * scale * stretch, top + glyph.offsetY * scale,
                            rec.width * scale * stretch, rec.height * scale)
        rl.draw_texture_pro(self._font.texture, rec, dest, rl.Vector2(0, 0), 0.0, color)
      x += (glyph.advanceX or rec.width) * scale * stretch
    return x - start

  def _draw_numeral(self, text: str, cx: float, cy: float, cap_px: float, color: rl.Color) -> None:
    scale = self._scale_for_cap(cap_px)
    zero_rec, _ = self._glyph("0")
    stretch = NUMERAL_ASPECT * cap_px / max(zero_rec.width * scale, 1.0)
    width = self._text_width(text, scale, stretch)
    self._draw_text(text, cx - width / 2, cy, scale, stretch, color)

  # ---- state --------------------------------------------------------------
  def _speed_text(self) -> str:
    sm = ui_state.sm
    if not (sm.alive["carState"] and sm.recv_frame["carState"] >= ui_state.started_frame):
      return "--"
    car_state = sm["carState"]
    self._cluster_speed_seen = self._cluster_speed_seen or car_state.vEgoCluster != 0.0
    v_ego = car_state.vEgoCluster if self._cluster_speed_seen else car_state.vEgo
    conversion = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    return str(max(int(round(v_ego * conversion)), 0))

  def _lateral_active(self) -> bool:
    sm = ui_state.sm
    return bool(sm.alive["carControl"] and sm.recv_frame["carControl"] >= ui_state.started_frame and
                sm["carControl"].latActive)

  def _wheel_texture(self, size: int):
    if self._wheel is None or self._wheel_size != size:
      self._wheel = gui_app.texture("icons_mici/wheel.png", size, size)
      self._wheel_size = size
    return self._wheel

  # ---- drawing ------------------------------------------------------------
  def _draw_hand(self, cx: float, cy: float, angle: float, length: float, h: float, color: rl.Color) -> None:
    a = math.radians(angle)
    ux, uy = math.sin(a), -math.cos(a)
    stem_end = rl.Vector2(cx + ux * STEM_LEN * h, cy + uy * STEM_LEN * h)
    tip = rl.Vector2(cx + ux * length * h, cy + uy * length * h)
    rl.draw_line_ex(rl.Vector2(cx, cy), stem_end, STEM_W * h, color)
    rl.draw_line_ex(stem_end, tip, HAND_W * h, color)
    rl.draw_circle_v(stem_end, HAND_W * h / 2, color)
    rl.draw_circle_v(tip, HAND_W * h / 2, color)

  def render(self, rect: rl.Rectangle, timestamp: float | None = None, night: bool | None = None) -> None:
    if self._started_frame != ui_state.started_frame:
      self._started_frame = ui_state.started_frame
      self._night.reset()
      self._cluster_speed_seen = False
      self._snap_blend = True
    ts = time.time() if timestamp is None else timestamp  # noqa: TID251  the face shows the wall clock
    if night is None:
      is_night = self._night.update(ts)
      known = self._night.known
    else:
      is_night, known = night, True
    if known and (self._snap_blend or night is not None):
      # The first decision of a drive (or a forced preview) shows at once, without a fade.
      self._blend.x = float(is_night)
      self._snap_blend = False
    t = self._blend.update(float(is_night))

    w, h = rect.width, rect.height
    cx, cy = rect.x + w / 2, rect.y + h / 2
    rl.draw_rectangle_rec(rect, rl.BLACK)

    for x0, y0, x1, y1, kind in dial_marks(w, h):
      color = mix(DAY[kind], NIGHT[kind], t)
      width = (HOUR_LINE_W if kind == "hour" else MINUTE_TICK_W) * h
      rl.draw_line_ex(rl.Vector2(cx + x0, cy + y0), rl.Vector2(cx + x1, cy + y1), width, color)

    numeral_color = mix(DAY["numeral"], NIGHT["numeral"], t)
    for value, fx, fy in NUMERALS:
      self._draw_numeral(str(value), cx + fx * w, cy + fy * h, NUMERAL_CAP * h, numeral_color)

    local = kst_now(ts)
    self._draw_complications(cx, cy, w, h, t, local)

    hour_a, minute_a, second_a = hand_angles(local, ts - math.floor(ts))
    hand_color = mix(DAY["hand"], NIGHT["hand"], t)
    self._draw_hand(cx, cy, hour_a, HOUR_HAND, h, hand_color)
    self._draw_hand(cx, cy, minute_a, MINUTE_HAND, h, hand_color)
    if t < 0.99:
      a = math.radians(second_a)
      tip = rl.Vector2(cx + math.sin(a) * SECOND_HAND * h, cy - math.cos(a) * SECOND_HAND * h)
      rl.draw_line_ex(rl.Vector2(cx, cy), tip, SECOND_W * h, mix(DAY["second"], NIGHT["second"], t, 1.0 - t))
    rl.draw_circle_v(rl.Vector2(cx, cy), HUB_R * h, mix(DAY["hub"], NIGHT["hub"], t))
    rl.draw_circle_v(rl.Vector2(cx, cy), HUB_HOLE_R * h, rl.BLACK)

  def _draw_complications(self, cx: float, cy: float, w: float, h: float, t: float, local: datetime) -> None:
    cap = COMPLICATION_CAP * h
    scale = self._scale_for_cap(cap)
    small = scale * 0.7
    text_color = mix(DAY["text"], NIGHT["text"], t)

    # Left: steering state + speed (the reference shows an alarm here).
    speed = self._speed_text()
    unit = "km/h" if ui_state.is_metric else "mph"
    icon = max(int(round(cap * 1.45)), 8)
    gap = cap * 0.35
    total = icon + gap + self._text_width(speed, scale, 1.0) + cap * 0.15 + self._text_width(unit, small, 1.0)
    x = cx + LEFT_COMPLICATION_X * w - total / 2
    lat_color = mix(DAY["lat_on"], NIGHT["lat_on"], t) if self._lateral_active() else mix(DAY["lat_off"], NIGHT["lat_off"], t)
    wheel = self._wheel_texture(icon)
    rl.draw_texture_pro(wheel, rl.Rectangle(0, 0, wheel.width, wheel.height),
                        rl.Rectangle(x, cy - icon / 2, icon, icon), rl.Vector2(0, 0), 0.0, lat_color)
    x += icon + gap
    x += self._draw_text(speed, x, cy, scale, 1.0, text_color) + cap * 0.15
    unit_cy = cy + (cap - cap * 0.7) / 2  # share the baseline with the speed digits
    self._draw_text(unit, x, unit_cy, small, 1.0, text_color)

    # Right: weekday + day of month.
    weekday = WEEKDAYS[local.weekday()]
    day = str(local.day)
    space = cap * 0.4
    total = self._text_width(weekday, scale, 1.0) + space + self._text_width(day, scale, 1.0)
    x = cx + RIGHT_COMPLICATION_X * w - total / 2
    x += self._draw_text(weekday, x, cy, scale, 1.0, mix(DAY["weekday"], NIGHT["weekday"], t)) + space
    self._draw_text(day, x, cy, scale, 1.0, text_color)
