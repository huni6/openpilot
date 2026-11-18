"""NewUi 3: a robot face that replaces the C4 onroad view.

Display only: alerts are still drawn on top by AugmentedRoadView, and nothing here
changes driving control. Motion and moods live in robot_face_model; this file reads
the car state for it and draws the "glowing eyes" face with raylib.
"""
import math
import time

import numpy as np
import pyray as rl

from openpilot.selfdrive.ui.mici.onroad.robot_face_music import CAPTURE_DELAY, BeatTracker, PhoneMusic, SteadyBeat
from openpilot.selfdrive.ui.mici.onroad.robot_face_model import (DM_ALERTS, FaceInputs, FaceModel, clamp, ease_out_back,
                                                                  ease_out_cubic, eye_span_arrays, fract, vnoise)
from openpilot.selfdrive.ui.mici.onroad.robot_face_model import ease_in_out_sine as ease_in_out
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight

GAP = 110          # eye centres are this far from the middle
EYE_Y = -6
EYE_SCALE = (0.92, 0.94)
SUPER_N = 3.2      # rounded-square eye outline
BANDS = 12         # the top-to-bottom gradient as solid bands: one strip draw each, not a call per vertex
GLOW_PX = 28       # the halo reaches this far past the eye's outline
GLOW_LAYERS = 7    # stacked, widening copies of the outline; their alphas add up toward the edge
GLOW_ALPHA = 0.064 # per layer, times the glow strength
GLOW_EDGE = GLOW_PX * np.array([1.0, 0.92, 0.8, 0.65, 0.5, 0.33, 0.16])   # halo columns past each side of the eye

# Tints the C4 screen still tells apart (the old near-white ones all read as white there): warm orange at rest,
# green while driving, amber when worried or straining, red for danger.
EYE_TOP, GLOW = (255, 226, 188), (255, 136, 48)
GREEN_EYE, GREEN_GLOW = (172, 255, 208), (30, 224, 132)
AMBER_EYE, AMBER_GLOW = (255, 210, 120), (255, 150, 0)
RED_EYE, RED_GLOW = (255, 160, 150), (255, 46, 40)
BOTTOM_MIX = 0.5   # how far the eye's lower part leans toward its glow colour

OK_GREEN, AMBER = (90, 235, 150), (255, 176, 40)   # the set speed rolling up / down

# the steering wheel along the bottom edge while it drives
WHEEL_R = 96        # rim radius
WHEEL_RIM = 8       # half its thickness
WHEEL_DROP = 52     # its centre is this far below the screen's bottom edge
WHEEL_HIDE = 74     # and slides this much further down when not driving
WHEEL_GAP = 6       # the mouth stays at least this far above the rim
HAND_AT = 38        # degrees from the top where its hands hold the rim
HAND_R = 13         # a hand's radius
# small steering angles (lane keeping is a few degrees) are drawn about three times bigger, easing into the turn where
# the outer hand reaches the bottom edge: the wheel only turns as far as its hands, still on screen, can take it
WHEEL_TURN_GAIN = 3.0
WHEEL_TURN_MAX = math.degrees(math.acos((WHEEL_DROP + HAND_R) / WHEEL_R)) - HAND_AT   # about 9 deg
# the cruise set speed: big rolling digits in place of the eyes (they shut to a line first, and open after it)
SPEED_FONT = 120
SPEED_ROLL_S = 0.4   # one digit rolling over to its new value
SPEED_SHUT_S = 0.18  # the eyes shutting before it shows, and opening after it
SPEED_OUT_S = 0.3    # the number shrinking away before they open
DIGIT_DROP = 0.077   # a digit's glyph sits this far (x font size) below the centre of the box _text centres
DIGIT_HALF = 0.39    # and half its height (0.35) plus a margin: the odometer window


def mix(a, b, t):
  return tuple(x + (y - x) * t for x, y in zip(a, b, strict=True))


def color(c, a=1.0) -> rl.Color:
  return rl.Color(int(clamp(c[0], 0, 255)), int(clamp(c[1], 0, 255)), int(clamp(c[2], 0, 255)), int(clamp(a, 0, 1) * 255))


def fade(c, a=1.0) -> rl.Color:
  """c at strength a over the black screen, as an opaque colour: thick strokes made of overlapping segments and
  joint discs stay even (a translucent stroke would show beads where they overlap)."""
  a = clamp(a, 0, 1)
  return color((c[0] * a, c[1] * a, c[2] * a), 1.0)


class RobotFace:
  def __init__(self):
    self.model = FaceModel()
    self._last = time.monotonic()
    self._font = gui_app.font(FontWeight.BOLD)
    self._beat = BeatTracker()
    self._phone = PhoneMusic()     # the phone's player, through the Navdy HUD
    self._steady = SteadyBeat()    # the groove while the phone plays and the microphone finds no beat
    self._mic = None           # every rawAudioData chunk (not just the latest), opened on first use
    self._glow_tex = None

  # ---- inputs -------------------------------------------------------------------------
  def _inputs(self) -> FaceInputs:
    sm = ui_state.sm

    def fresh(name):
      return sm.alive[name] and sm.valid[name] and sm.recv_frame[name] >= ui_state.started_frame

    def seen(name):
      # sent on change or slowly (events, navigation): only ask that it belongs to this drive
      return sm.recv_frame[name] >= ui_state.started_frame
    cs, ss = sm['carState'], sm['selfdriveState']
    car_ok, sd_ok = fresh('carState'), fresh('selfdriveState')
    alert = str(ss.alertType).split('/')[0] if sd_ok else ''
    lat = fresh('carControl') and sm['carControl'].latActive
    v_ego = float(cs.vEgo) if car_ok else 0.0
    inp = FaceInputs(
      session=ui_state.started_frame,
      engaged=bool(sd_ok and ss.enabled) or bool(lat),
      left_blinker=bool(car_ok and cs.leftBlinker),
      right_blinker=bool(car_ok and cs.rightBlinker),
      steer_deg=-float(cs.steeringAngleDeg) if car_ok else 0.0,   # carState is + left; the face wants + right
      v_ego=v_ego,
      a_ego=float(cs.aEgo) if car_ok else 0.0,
      standstill=bool(car_ok and cs.standstill),
      dm_alert=alert.startswith(DM_ALERTS),
      fcw='fcw' in alert.lower() or alert.startswith('forwardCollision'),
      blindspot_left=bool(car_ok and cs.leftBlindspot),
      blindspot_right=bool(car_ok and cs.rightBlindspot),
      gas=bool(car_ok and cs.gasPressed),
      hazard=bool(car_ok and cs.leftBlinker and cs.rightBlinker),
      gear=str(cs.gearShifter) if car_ok else '',
      seatbelt_off=bool(car_ok and cs.seatbeltUnlatched),
      override=bool(car_ok and cs.steeringPressed),
      set_speed=float(cs.vCruiseCluster or cs.vCruise) if car_ok else 0.0,
    )
    if fresh('modelV2'):
      meta = sm['modelV2'].meta
      state, direction = str(meta.laneChangeState), str(meta.laneChangeDirection).lower()
      inp.lane_phase = 1 if state == 'laneChangeStarting' else 2 if state == 'laneChangeFinishing' else 0
      inp.lane_dir = -1 if 'left' in direction else 1 if 'right' in direction else 0
    if fresh('radarState'):
      lead = sm['radarState'].leadOne
      if lead.status:
        inp.lead_d, inp.lead_v = float(lead.dRel), float(lead.vLead)
    if lat:
      # the torque actually sent (1.0 = the car's limit), else what the controller asked for; + left there, + right here
      out = sm['carOutput'] if fresh('carOutput') else None
      inp.steer_torque = -float(out.actuatorsOutput.torque if out is not None else sm['carControl'].actuators.torque)
    if fresh('controlsState'):
      inp.lat_accel = -float(sm['controlsState'].curvature) * v_ego * v_ego   # curvature is + right here; + left wanted
    # one-frame events can fall between UI frames; their alerts stay up for a second or two
    names = {str(e.name) for e in sm['onroadEvents']} if seen('onroadEvents') else set()
    inp.cutin = 'radarCutin' in names or alert == 'radarCutin'
    inp.slow_steer = 'belowSteerSpeed' in names or alert == 'belowSteerSpeed'
    return inp

  # ---- drawing helpers -------------------------------------------------------------------
  def _glow(self):
    if self._glow_tex is None:
      img = rl.gen_image_gradient_radial(128, 128, 0.0, rl.WHITE, rl.Color(255, 255, 255, 0))
      self._glow_tex = rl.load_texture_from_image(img)
      rl.unload_image(img)
    return self._glow_tex

  def _blob(self, x, y, w, h, c, a):
    """Soft radial glow: one textured quad (4 vertices)."""
    tex = self._glow()
    rl.draw_texture_pro(tex, rl.Rectangle(0, 0, tex.width, tex.height), rl.Rectangle(x - w / 2, y - h / 2, w, h),
                        rl.Vector2(0, 0), 0.0, color(c, a))

  @staticmethod
  def _poly(points, c):
    """Filled polygon as a fan from its centroid (shapes here are star-shaped around it).

    Wound counter-clockwise on screen, the way raylib's front faces go: most outlines here run clockwise, and
    whenever the batch reached the GPU with face culling on they were dropped (stars, hearts, sparkles and
    arrowheads vanished on the device; a star showed only its glow, as a blur). draw() also flushes the batch
    before it turns culling back on."""
    if len(points) < 3:
      return
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, [*points[1:], points[0]], strict=True))
    if area > 0:            # clockwise on screen (y down)
      points = points[::-1]
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    fan = [rl.Vector2(cx, cy), *(rl.Vector2(x, y) for x, y in points), rl.Vector2(*points[0])]
    rl.draw_triangle_fan(fan, len(fan), color(c[:3], c[3] if len(c) > 3 else 1.0))

  @staticmethod
  def _stroke(points, thick, c, a=1.0, opaque=False):
    col = fade(c, a) if opaque else color(c, a)
    for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
      rl.draw_line_ex(rl.Vector2(x1, y1), rl.Vector2(x2, y2), thick, col)
    for x, y in points:
      rl.draw_circle_v(rl.Vector2(x, y), thick / 2, col)

  @staticmethod
  def _ribbon(points, thick, c, a=1.0):
    """A thick polyline as one triangle strip (no overlapping pieces, so it can be translucent), round ends."""
    if len(points) < 2 or a <= 0.005:
      return
    half = thick / 2
    strip = []
    for i, (x, y) in enumerate(points):
      x0, y0 = points[max(i - 1, 0)]
      x1, y1 = points[min(i + 1, len(points) - 1)]
      dx, dy = x1 - x0, y1 - y0
      n = math.hypot(dx, dy) or 1.0
      nx, ny = -dy / n * half, dx / n * half
      strip += [rl.Vector2(x + nx, y + ny), rl.Vector2(x - nx, y - ny)]
    col = color(c, a)
    rl.draw_triangle_strip(strip, len(strip), col)
    for (x, y), (px, py) in ((points[0], points[1]), (points[-1], points[-2])):
      out = math.degrees(math.atan2(y - py, x - px))       # half a disc on the outer side only: no doubled overlap
      rl.draw_circle_sector(rl.Vector2(x, y), half, out - 90, out + 90, 10, col)

  @staticmethod
  def _quad(x0, y0, cx, cy, x1, y1, n=14):
    return [((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x1, (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y1)
            for t in (i / n for i in range(n + 1))]

  # ---- the eye ---------------------------------------------------------------------------
  @staticmethod
  def _draw_strips(pts: np.ndarray, colors) -> None:
    """One triangle strip per row of pts (rows, n, 2), all from one vertex buffer: a C call per strip, not per vertex."""
    rows, n = pts.shape[0], pts.shape[1]
    ffi = getattr(rl, 'ffi', None)
    if ffi is None:  # recording stub in tests
      for row, col in zip(pts, colors, strict=True):
        rl.draw_triangle_strip([rl.Vector2(float(x), float(y)) for x, y in row], n, col)
      return
    buf = ffi.new('Vector2[]', rows * n)
    np.frombuffer(ffi.buffer(buf), dtype=np.float32)[:] = pts.ravel()
    base = ffi.cast('Vector2 *', buf)  # a bare array would be wrapped as Vector2(*)[]
    for r, col in enumerate(colors):
      rl.draw_triangle_strip(base + r * n, n, col)

  def _eye_geometry(self, side, ex, ey, w, h, up, low, tilt):
    P = self.model.P
    xs, top, bottom, lid, keep = eye_span_arrays(ex, ey, w, h, SUPER_N, up, low, tilt, P['smile'].v, P['sag'].v, side)
    if not keep.any():
      return None
    return xs, top, bottom, lid, keep, float(top[keep].min()), float(bottom[keep].max())

  def _eye_glow(self, geo, glow_c, glow_a):
    """A halo that follows the visible outline, so a crescent or a half-shut eye glows in its own shape.
    (A plain ellipse filled the part the lids cut away and read as a blurred eye.) Each layer is the outline
    grown by a disc of radius pad; the thin layers add up to a soft falloff."""
    xs, top, bottom, _, keep = geo[:5]
    idx = np.flatnonzero(keep)
    col = color(glow_c, glow_a * GLOW_ALPHA)
    pads = np.linspace(GLOW_PX, GLOW_PX / GLOW_LAYERS, GLOW_LAYERS)[:, None, None]
    for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1):
      if run.size < 2:
        continue
      sl = slice(run[0], run[-1] + 1)
      cols = np.unique(np.r_[run[0]:run[-1] + 1:2, run[-1]])   # every other column is plenty for a soft halo
      x, t, b = xs[cols], top[cols], bottom[cols]
      # one shared column grid for every layer: the eye's own columns plus a fan past each side
      xo = np.concatenate((x[0] - GLOW_EDGE, x, x[-1] + GLOW_EDGE[::-1]))
      dx = x[None, None, :] - xo[None, :, None]
      rise = np.sqrt(np.maximum(pads * pads - dx * dx, 0.0))
      near = np.abs(dx) <= pads
      to = np.where(near, t - rise, np.inf).min(axis=2)
      bo = np.where(near, b + rise, -np.inf).max(axis=2)
      mid = np.interp(xo, x, (t + b) / 2)                   # columns a layer does not reach collapse onto the middle
      reach = np.isfinite(bo)
      to, bo = np.where(reach, np.minimum(to, bo), mid), np.where(reach, bo, mid)
      pts = np.empty((GLOW_LAYERS, xo.size * 2, 2), np.float32)
      pts[:, 0::2, 0] = pts[:, 1::2, 0] = xo
      pts[:, 0::2, 1] = to
      pts[:, 1::2, 1] = bo
      self._draw_strips(pts, [col] * GLOW_LAYERS)

  def _eye(self, ex, ey, w, h, geo, gx, gy, col_top, col_bottom, shimmer):
    xs, top, bottom, _, keep = geo[:5]
    y0 = ey - h / 2
    # the top-to-bottom gradient as BANDS solid bands, each the eye clipped to its slice of the height
    edges = y0 + h * np.arange(1, BANDS) / BANDS
    lo = np.concatenate(([-np.inf], edges))[:, None]
    hi = np.concatenate((edges, [np.inf]))[:, None]
    t_all, b_all = np.clip(top, lo, hi), np.clip(bottom, lo, hi)
    band_cols = [color(mix(col_top, col_bottom, (k + 0.5) / BANDS)) for k in range(BANDS)]
    # one strip set per unbroken run of visible columns (normally one): its ends are the eye's straight sides
    idx = np.flatnonzero(keep)
    for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1):
      if run.size < 2:
        continue
      sl = slice(run[0], run[-1] + 1)
      t, b = t_all[:, sl], b_all[:, sl]
      rows = np.flatnonzero((b - t).max(axis=1) > 0.05)
      t, b, cols = t[rows], b[rows], [band_cols[k] for k in rows]
      pts = np.empty((len(cols), (run[-1] - run[0] + 1) * 2, 2), np.float32)
      pts[:, 0::2, 0] = pts[:, 1::2, 0] = xs[sl]
      pts[:, 0::2, 1] = t
      pts[:, 1::2, 1] = b
      self._draw_strips(pts, cols)
    # catchlights, drifting against the gaze; only where the eye is open there
    for fx, fy, rx, ry, a in ((0.3, 0.25, 0.1, 0.078, 0.86), (0.42, 0.36, 0.042, 0.036, 0.5)):
      px, py = ex - w / 2 + w * fx - gx * 6, y0 + h * fy - gy * 4
      i = int(np.argmin(np.abs(xs - px)))
      if keep[i] and top[i] + h * ry < py < bottom[i] - h * ry:
        rl.draw_ellipse(int(px), int(py), w * rx, h * ry, rl.Color(255, 255, 255, int(255 * a * shimmer)))

  def _special_eye(self, shape, side, ex, ey, col_eye, glow_c, glow_a):
    m = self.model
    big = 1.6
    if shape == 'star':
      k = clamp((m.clock - m.star_from) / 0.16, 0, 1)
      s = ease_out_back(k) * big
      rot = math.sin(m.clock * 9 + side) * 0.18

      def star(grow):
        return [(ex + math.cos(-math.pi / 2 + i * math.pi / 5 + rot) * (13 if i % 2 else 30) * s * grow,
                 ey + math.sin(-math.pi / 2 + i * math.pi / 5 + rot) * (13 if i % 2 else 30) * s * grow) for i in range(10)]
      # the halo is the star itself, grown in a few steps: crisp edges (a soft round blob behind it read as a blur)
      halo = mix(glow_c, col_eye, 0.45)                       # a light halo, not a dark rim
      for grow, a in ((1.5, 0.22), (1.3, 0.45), (1.13, 0.8)):   # brightest next to the star: a glow, not an outline
        self._poly(star(grow), (*halo, a * glow_a / 0.58))
      self._poly(star(1.0), (*col_eye, 1.0))
    elif shape == 'heart':
      s = 25 * big * (1 + math.sin(m.clock * 11) * 0.08)

      def heart(grow):
        out = []
        for i in range(40):
          t = i / 40 * 2 * math.pi
          out.append((ex + 16 * math.sin(t) ** 3 / 16 * s * grow,
                      ey + 4 - (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)) / 16 * s * grow))
        return out
      for grow, a in ((1.45, 0.22), (1.25, 0.45), (1.1, 0.8)):
        self._poly(heart(grow), (255, 110, 155, a))
      self._poly(heart(1.0), (255, 120, 165, 1.0))

  # ---- main draw ---------------------------------------------------------------------------
  def render(self, rect: rl.Rectangle) -> None:
    now = time.monotonic()
    dt, self._last = now - self._last, now
    m = self.model
    inp = self._inputs()
    self._listen(dt, inp)
    m.update(dt, inp)
    self.draw(rect)

  def _audio_chunks(self) -> list[tuple[np.ndarray, float]]:
    """New microphone chunks with the monotonic time micd stamped each one (they can arrive in bursts)."""
    from cereal import messaging
    if self._mic is None:
      self._mic = messaging.sub_sock('rawAudioData', conflate=False)
    return [(np.frombuffer(msg.rawAudioData.data, dtype=np.int16), msg.logMonoTime * 1e-9) for msg in messaging.drain_sock(self._mic)]

  def _listen(self, dt: float, inp: FaceInputs) -> None:
    """Music: dance to the beat the cabin microphone finds (micd, 50 ms chunks), or else to a steady 120 BPM while
    the phone says a song is playing (Navdy HUD v186+). The car's own alert sound is never taken for music."""
    beats = self._beat.advance(dt)          # the clock moves to now first; the new audio then corrects it
    steady = self._steady.advance(dt)
    chunks = self._audio_chunks()
    now = time.monotonic()
    heard = False
    if str(ui_state.sm['selfdriveState'].alertSound) in ('none', '0', ''):
      for chunk, stamped in chunks:
        self._beat.feed(chunk, age=max(0.0, now - stamped) + CAPTURE_DELAY)
      heard = self._beat.locked
    if heard:
      inp.music_on, inp.music_beats, inp.music_period = True, tuple(beats), self._beat.period
    elif self._phone.playing(now):
      inp.music_on, inp.music_beats, inp.music_period = True, tuple(steady), self._steady.period

  def draw(self, rect: rl.Rectangle) -> None:
    m, P = self.model, self.model.P
    rl.draw_rectangle_rec(rect, rl.BLACK)

    # straining on the wheel: grows with the steering torque, all-out (eyes shut tight) near the limit
    effort = m.effort
    shut = clamp((effort - 0.8) / 0.12, 0, 1)
    tremble = max(m.tremble, effort * 0.9)

    # palette
    eye, glow = EYE_TOP, GLOW
    eye, glow = mix(eye, GREEN_EYE, P['green'].v), mix(glow, GREEN_GLOW, P['green'].v)
    amber = clamp(max(P['amber'].v, effort * 0.5), 0, 1)
    eye, glow = mix(eye, AMBER_EYE, amber), mix(glow, AMBER_GLOW, amber)
    red = clamp(P['red'].v, 0, 1)
    eye, glow = mix(eye, RED_EYE, red), mix(glow, RED_GLOW, red)
    f = clamp(P['flash'].v, 0, 1)
    eye, glow = mix(eye, (255, 255, 255), f), mix(glow, (255, 245, 235), f * 0.8)
    bottom = mix(eye, glow, BOTTOM_MIX)
    bright = clamp(P['bright'].v, 0, 1)
    eye_d, bottom_d, glow_d = mix((0, 0, 0), eye, bright), mix((0, 0, 0), bottom, bright), glow

    # whole-face motion
    clock = m.clock
    pulse = max(0.0, 1 - fract(m.tick_phase) * 3.2) if m.signal else 0.0
    trem = (math.sin(clock * 57) * 1.1 + math.sin(clock * 31) * 0.7) * tremble if tremble else 0.0
    sway_x, sway_y, sway_r = vnoise(clock * 0.33, 1) * 0.7, vnoise(clock * 0.29, 2) * 0.5, vnoise(clock * 0.23, 3) * 0.3
    sleep_sway = math.sin(clock * 0.9) * 2.5 if m.sleep_stage == 2 else 0.0
    ride = math.sin(clock * 2.3) * 0.6 + vnoise(clock * 1.6, 21) * 0.9 if m.engaged and not m.sleep_stage else 0.0
    arc = min(abs(P['hx'].vel + P['ex'].vel) * 0.025, 4)
    hx = P['hx'].v + P['ex'].v + m.osc_offset('hx') + trem + sway_x + m.effort_side * effort * 16
    hy = P['hy'].v + P['ey'].v + m.osc_offset('hy') - m.breath * 2 + sway_y + arc - pulse * 0.8 + ride
    rot = P['rot'].v + P['er'].v + m.osc_offset('rot') + sway_r + sleep_sway + m.effort_side * effort * 5
    stretch = clamp(-P['hy'].vel * 0.00028, -0.11, 0.13)
    pitch = 1 + P['pitch'].v
    sy = (1 + stretch) * (1 + m.breath * 0.012) * pitch
    sx = (1 - stretch * 0.6) * (1 + m.breath * 0.005) * pitch
    shake_x = vnoise(clock * 91, 31) * m.shake
    shake_y = vnoise(clock * 77, 37) * m.shake * 0.7

    rl.rl_disable_backface_culling()
    wheel_top, _, wheel_on = self._wheel(rect, eye_d, bottom_d, glow_d, effort)
    tx, ty = rect.x + rect.width / 2 + hx + shake_x, rect.y + rect.height / 2 + hy + shake_y
    cos_r, sin_r = math.cos(math.radians(rot)), math.sin(math.radians(rot))

    def above_wheel(mx, my, half):
      """The mouth's y (face coordinates), raised as needed so it never touches the wheel on screen."""
      if wheel_on < 0.05:
        return my
      return min(my, (wheel_top - WHEEL_GAP - ty - sin_r * sx * mx) / max(cos_r * sy, 0.3) - half)

    # the set speed takes the eyes' place: the face shuts to a line first and opens again after it
    speed = next((p for p in m.parts if p.kind == 'speed' and p.age >= p.delay), None)
    shut_for_speed = 0.0
    if speed is not None:
      shut_for_speed = ease_in_out(clamp(min((speed.age - speed.delay) / SPEED_SHUT_S, (speed.life - speed.age) / SPEED_SHUT_S), 0, 1))
    rl.rl_push_matrix()
    rl.rl_translatef(tx, ty, 0)
    rl.rl_rotatef(rot, 0, 0, 1)
    if shut_for_speed >= 1.0:     # fully shut: nothing of the face is drawn (not even a line), so it never meets the number
      rl.rl_scalef(0.0, 0.0, 1)
    else:
      rl.rl_scalef(sx * (1 - 0.5 * shut_for_speed), sy * (1 - shut_for_speed), 1)

    # blush
    if P['blush'].v > 0.01:
      for s in (-1, 1):
        self._blob(s * 148, 40, 84, 50, (255, 130, 155), 0.7 * P['blush'].v * bright)

    glow_a = (0.58 + m.breath * 0.05 + pulse * 0.25 + (math.sin(clock * 6) * 0.5 + 0.5) * 0.25 * m.dm) * bright
    glow_a *= 1 + vnoise(clock * 3, 6) * 0.03
    shimmer = 0.92 + vnoise(clock * 2.4, 8) * 0.08
    relaxed = (1 - m.attn) * 0.14
    shape = m.eye_shape or ('star' if clock < m.star_until else None)
    eyes = []
    for side in (-1, 1):
      left = side < 0
      gx = P['gxL'].v if left else P['gxR'].v
      gy = P['gyL'].v if left else P['gyR'].v
      gvel = P['gxL'].vel if left else P['gxR'].vel
      blink = m.blink_l if left else m.blink_r
      wink = P['wL'].v if left else P['wR'].v
      ex = side * (GAP - abs(gx) * GAP * 0.05) + gx * GAP * 0.22
      ey = EYE_Y + gy * 16 + blink * 3
      if shape:
        self._special_eye(shape, side, ex, ey, eye_d, glow_d, glow_a)
        continue
      persp = 1 + side * gx * 0.07 - side * P['asym'].v
      sacc = min(abs(gvel) * 0.011, 0.07)
      squash = blink * 0.1
      w = P['w'].v * EYE_SCALE[0] * persp * (1 + sacc + squash * 0.6) * (1.012 if left else 1)
      h = P['h'].v * EYE_SCALE[1] * persp * (1 - squash) * (1 - sacc * 0.4) * (1 + m.breath * 0.008) * (1 if left else 1.008)
      lid_gaze = max(0.0, gy) * 0.17 - max(0.0, -gy) * 0.06
      flutter = vnoise(clock * 2.2, 7 if left else 11) * 0.006 + (math.sin(clock * 40 + side) * 0.012 if tremble else 0)
      up_base = clamp(P['up'].v + relaxed + lid_gaze + flutter + wink * 1.1 + effort * 0.3 + shut * 0.7, 0, 1)
      up = up_base + (1 - up_base) * blink
      low = P['low'].v + blink * 0.08 + max(0.0, -gy) * 0.04 + wink * 0.25 + effort * 0.12
      geo = self._eye_geometry(side, ex, ey, w, h, up, low, P['tilt'].v + effort * 0.4)
      eyes.append((side, ex, ey, w, h, gx, gy, up_base, wink, geo))

    # where each eye's outer edge is this frame: the headphone cups sit just beyond it, whatever the eyes do
    self._eye_edge = {side: side * ex + w * 0.5 for side, ex, ey, w, h, *_ in eyes}
    # every glow first, then the bodies, so one eye's halo never lies over the other eye
    for *_, geo in eyes:
      if geo is not None:
        self._eye_glow(geo, glow_d, glow_a)
    for side, ex, ey, w, h, gx, gy, up_base, wink, geo in eyes:
      left = side < 0
      if geo is not None:
        self._eye(ex, ey, w, h, geo, gx, gy, eye_d, bottom_d, shimmer)
      closed = clamp((up_base - 0.9) / 0.08, 0, 1)
      if closed > 0:
        # eyes held shut (dozing, sleeping, a yawn, a wink) show a stroke instead of vanishing
        if m.squeeze or shut > 0.5:
          d = 1 if left else -1
          pts = [(ex - d * w * 0.22, ey - 11), (ex + d * w * 0.2, ey + 4), (ex - d * w * 0.22, ey + 19)]
        else:
          happy = P['smile'].v > 0.5 or wink > 0.5
          yy = ey + 6 if happy else (geo[6] if geo is not None else ey + h * 0.3) + 2
          bend = -14 if happy else 14 + m.breath * 2
          pts = self._quad(ex - w * 0.36, yy, ex, yy + bend, ex + w * 0.36, yy)
        # a neon halo in the stroke's own shape (a soft round blob here read as a blur)
        self._ribbon(pts, 20, glow_d, 0.16 * closed * bright)
        self._ribbon(pts, 14, glow_d, 0.24 * closed * bright)
        self._stroke(pts, 8, eye_d, 0.92 * closed)

    # gritted teeth while straining on the wheel
    g = clamp((effort - 0.35) / 0.3, 0, 1)
    if g > 0.01:
      mx = (P['gxL'].v + P['gxR'].v) * 7 + m.effort_side * effort * 4
      my = above_wheel(mx, 62 + math.sin(clock * 45) * 0.8 * effort, 6)
      hw = 14 + effort * 4
      zig = [(mx + hw * (i / 3 - 1), my + (3.5 if i % 2 else -3.5) * (0.6 + effort * 0.4)) for i in range(7)]
      self._stroke(zig, 4, eye_d, 0.9 * g)

    # mouth only when it opens: surprise, a yawn, a sneeze, a sigh (gritted teeth win while straining)
    o = clamp(P['mO'].v, 0, 1) * (1 - g)
    if o > 0.12:
      mx = (P['gxL'].v + P['gxR'].v) * 7
      my = above_wheel(mx, 64 + (P['gyL'].v + P['gyR'].v) * 4, 5 + o * 10)
      rl.draw_ellipse(int(mx), int(my + 2), 6 + o * 6, 3 + o * 10, color(eye_d, o))
      rl.draw_ellipse(int(mx), int(my + 3), 3 + o * 3.6, 1.5 + o * 6.5, rl.Color(24, 10, 4, int(255 * o * 0.85)))

    self._above_wheel = above_wheel     # the whistle sits where the mouth would, above the wheel too
    self._props(eye_d, glow_d)
    self._particles(eye_d, glow_d)
    rl.rl_pop_matrix()
    if speed is not None:
      self._speed(speed, eye_d, rect)
    # raylib batches shapes and sends them to the GPU later (when the batch fills, or at the end of the frame): send
    # them now, while culling is still off, so no strip or fan is dropped for its winding
    rl.rl_draw_render_batch_active()
    rl.rl_enable_backface_culling()

  def _wheel(self, rect, ink, body, glow, effort):
    """The top of a steering wheel along the bottom edge while it drives, turning with the car's wheel. Its two hands
    hold the rim, and let go (raised) while the driver has it. Screen space, behind the face.
    Returns the rim's top on screen, its centre y and how far it is up (0 hidden .. 1)."""
    m, P = self.model, self.model.P
    dv = clamp(P['drive'].v, 0, 1.2)
    cx = rect.x + rect.width / 2
    cy = rect.y + rect.height + WHEEL_DROP + (1 - min(dv, 1.0)) * WHEEL_HIDE
    top = cy - WHEEL_R - WHEEL_RIM
    if dv < 0.02 or top >= rect.y + rect.height:
      return top, cy, 0.0
    a = clamp(dv, 0, 1)
    c = rl.Vector2(cx, cy)
    inner, outer = WHEEL_R - WHEEL_RIM, WHEEL_R + WHEEL_RIM
    for pad, al in ((9, 0.08), (5, 0.12), (2, 0.18)):          # a soft halo in steps (crisp, no blurred texture)
      rl.draw_ring(c, inner - pad, outer + pad, 180, 360, 48, color(glow, al * a))
    rl.draw_ring(c, inner, outer, 180, 360, 48, rl.Color(20, 20, 20, int(250 * a)))
    for r in (inner, outer):
      rl.draw_ring(c, r - 1.2, r + 1.2, 180, 360, 48, color(ink, 0.7 * a))
    turn = WHEEL_TURN_MAX * math.tanh(WHEEL_TURN_GAIN * P['steer'].v / WHEEL_TURN_MAX)   # smooth up to the hands' reach
    turn += math.sin(m.clock * 43) * 1.6 * effort if effort > 0.2 else 0.0
    th = math.radians(turn)
    rl.draw_rectangle_pro(rl.Rectangle(cx + math.sin(th) * WHEEL_R, cy - math.cos(th) * WHEEL_R, 9, 2 * WHEEL_RIM + 2),
                          rl.Vector2(4.5, WHEEL_RIM + 1), turn, color(ink, 0.95 * a))   # the 12 o'clock stripe
    lift = 1 - clamp(P['hands'].v, 0, 1)
    for side in (-1, 1):
      ang = math.radians(turn + side * HAND_AT)
      hx = cx + math.sin(ang) * WHEEL_R + side * lift * 14 + math.sin(m.clock * 37 + side) * 1.2 * effort
      hy = cy - math.cos(ang) * WHEEL_R - lift * 34 + math.sin(m.clock * 9 + side) * 3 * lift   # raised hands: "all yours"
      ha = a * (1 - 0.25 * lift) * (1 - clamp(P['p_buckle'].v, 0, 1) if side < 0 else 1.0)   # holding the buckle
      if ha < 0.02:
        continue
      rl.draw_circle_v(rl.Vector2(hx, hy), 18, color(glow, 0.16 * ha))
      rl.draw_circle_v(rl.Vector2(hx, hy), HAND_R, color(body, ha))
      rl.draw_circle_v(rl.Vector2(hx - side * 7, hy - 8), 6, color(ink, ha))   # the thumb over the rim
    return top, cy, a

  # ---- icons that say what an event means ---------------------------------------------------
  def _text(self, txt, x, y, size, col):
    tw = rl.measure_text_ex(self._font, txt, size, 0)
    rl.draw_text_ex(self._font, txt, rl.Vector2(x - tw.x / 2, y - tw.y / 2), size, 0, col)

  def _digit(self, ch, x, y, size, ink, a):
    if ch and a > 0.01:
      self._text(ch, x, y, size, color(ink, min(a, 1.0)))

  def _speed(self, p, eye, rect):
    """The cruise set speed, big in the middle of the screen while the eyes are shut (screen coordinates).

    On engagement its digits drop in one by one, left to right, with a sparkle at each top corner. On a change each
    changed digit rolls over like an odometer, the way the speed went (up: the old digit rises out and the new one
    comes up from below; down: the reverse); the number pops, the new digits come in green (up) or amber (down) and
    settle into the eye colour, the glow flares and chevrons stream above it (up: rising away; down: sinking into it).
    At the end it shrinks away, and then the eyes open."""
    t0 = p.delay + SPEED_SHUT_S                                  # the eyes are shut from here
    if p.age < t0:
      return
    d = p.direction
    t = p.age - max(p.since, t0)                                 # into the current roll (or the drop-in)
    accent = eye if d == 0 else OK_GREEN if d > 0 else AMBER
    new = str(int(round(p.value)))
    old = str(int(round(p.prev))) if p.prev > 0 else ''
    n = len(new)
    out = clamp(1 - (p.life - p.age - SPEED_SHUT_S) / SPEED_OUT_S, 0, 1)
    if out >= 1:
      return
    grow = ease_out_back(clamp((p.age - t0) / 0.3, 0, 1))        # pops in as the eyes finish shutting
    pop = 1 + 0.08 * max(0.0, 1 - t / 0.35) ** 2 if d else 1.0
    size = SPEED_FONT * pop * (0.7 + 0.3 * grow) * (1 - 0.35 * ease_in_out(out))
    fade_all = clamp((p.age - t0) / 0.12, 0, 1) * (1 - out)
    cell = rl.measure_text_ex(self._font, '0', size, 0).x
    x, yc = rect.x + rect.width / 2 + p.x, rect.y + rect.height / 2 + EYE_Y + p.y   # where the digits' middle sits
    y = yc - DIGIT_DROP * size                                   # the text box centre that puts them there
    half = n * cell / 2
    flare = max(0.0, 1 - t / 0.6)
    self._blob(x, yc, half * 2 + 110, size * 1.4, accent, (0.18 + 0.32 * flare) * fade_all)
    ink = mix(accent, eye, ease_in_out(clamp(t / 0.9, 0, 1))) if d else eye
    win = DIGIT_HALF * size
    shift = 2 * win + 2                                          # neighbours on the strip: one is always out of the window
    # the digits roll inside a window the digits' own height, like an odometer: the old and the new digit sit one
    # above the other on one strip that moves as a whole, so they never overlap
    rl.begin_scissor_mode(int(x - half - 8), int(yc - win), int(half * 2 + 16), int(2 * win))
    for i in range(max(n, len(old))):
      nd = new[-1 - i] if i < n else ''
      od = old[-1 - i] if i < len(old) else ''
      cx = x + half - (i + 0.5) * cell
      if d == 0:                  # engagement: dropping in one by one from the left
        e = ease_out_back(clamp((t - (n - 1 - i) * 0.09) / 0.45, 0, 1))
        self._digit(nd, cx, y - shift * (1 - e), size, eye, fade_all)
      elif nd == od:
        self._digit(nd, cx, y, size, eye, fade_all)
      else:                       # rolling over (the ones first, then each place a beat later)
        e = ease_out_back(clamp((t - i * 0.07) / SPEED_ROLL_S, 0, 1))
        self._digit(od, cx, y - d * shift * e, size, eye, fade_all)
        self._digit(nd, cx, y + d * shift * (1 - e), size, ink, fade_all)
    rl.end_scissor_mode()
    top = yc - win - 4            # just above the digits
    if d and t < 1.3:             # chevrons above it: rising away (up, green) or sinking into it (down, amber)
      env = min(t / 0.15, 1.0) * clamp((1.3 - t) / 0.35, 0, 1) * fade_all
      for j in range(3):
        ph = fract(t * 1.6 + j / 3)
        cy = top - 8 - (ph if d > 0 else 1 - ph) * 30
        self._ribbon([(x - 14, cy + d * 6), (x, cy - d * 6), (x + 14, cy + d * 6)], 5, accent,   # translucent over the glow
                     env * math.sin(ph * math.pi))
    elif d == 0 and 0.2 < t < 1.1:  # engagement: a sparkle at each top corner
      k = (t - 0.2) / 0.9
      r = 14 * math.sin(k * math.pi)
      for side in (-1, 1):
        sx, sy = x + side * (half + 6), top + 6
        self._poly([(sx + math.cos(j * math.pi / 4 + k * 2) * (r if j % 2 == 0 else r * 0.3),
                     sy + math.sin(j * math.pi / 4 + k * 2) * (r if j % 2 == 0 else r * 0.3)) for j in range(8)],
                   (*eye, fade_all))

  def _props(self, eye, glow):
    m, P = self.model, self.model.P
    eye_top = EYE_Y - P['h'].v * EYE_SCALE[1] * 0.5
    white = color(eye)

    # sunglasses slide down over the eyes
    v = clamp(P['p_shades'].v, 0, 1.15)
    if v > 0.01:
      lw, lh = max(70, P['w'].v * EYE_SCALE[0] * 1.3), max(40, P['h'].v * EYE_SCALE[1] * 0.8)
      y = EYE_Y + (1 - min(v, 1)) * -70
      a = clamp(v, 0, 1)
      for s in (-1, 1):
        r = rl.Rectangle(s * GAP - lw / 2, y - lh / 2, lw, lh)
        rl.draw_rectangle_rounded(r, 0.35, 8, rl.Color(5, 5, 5, int(255 * a)))
        rl.draw_rectangle_rounded_lines_ex(r, 0.35, 8, 4.5, color(eye, a))
        gk = fract(m.clock * 0.55)
        gx0 = s * GAP - lw / 2 + 10 + gk * (lw - 30)
        rl.draw_line_ex(rl.Vector2(gx0, y - lh / 2 + 8), rl.Vector2(gx0 - 12, y + lh / 2 - 8), 4, color(eye, a * 0.7))
      self._stroke(self._quad(-GAP + lw / 2, y - 6, 0, y - 15, GAP - lw / 2, y - 6, 8), 4.5, eye, a)
    # question mark
    v = P['p_quest'].v
    if v > 0.01:
      x = (1 if P['er'].v >= 0 else -1) * (GAP + 70)
      size = 40 * max(v, 0.01)
      tw = rl.measure_text_ex(self._font, "?", size, 0)
      rl.draw_text_ex(self._font, "?", rl.Vector2(x - tw.x / 2, -80 - tw.y / 2), size, 0, color(eye, clamp(v, 0, 1)))
    # light bulb: an idea
    v = P['p_bulb'].v
    if v > 0.01:
      on = 0.6 + 0.4 * math.sin(m.clock * 12)
      x, y, s = 152, -84, max(v, 0.01)
      self._blob(x, y - 6 * s, 70 * s, 70 * s, (255, 230, 120), 0.5 * on * clamp(v, 0, 1))
      rl.draw_circle_v(rl.Vector2(x, y - 6 * s), 13 * s, rl.Color(255, 225, 110, int(255 * clamp(v, 0, 1) * (0.6 + 0.35 * on))))
      rl.draw_rectangle_rec(rl.Rectangle(x - 6 * s, y + 6 * s, 12 * s, 9 * s), color(eye, clamp(v, 0, 1)))
      for a in (-2.5, -1.57, -0.64):
        rl.draw_line_ex(rl.Vector2(x + math.cos(a) * 20 * s, y - 6 * s + math.sin(a) * 20 * s),
                        rl.Vector2(x + math.cos(a) * 27 * s, y - 6 * s + math.sin(a) * 27 * s), 3, color(eye, clamp(v, 0, 1)))
    # nightcap while asleep
    v = P['p_nightcap'].v
    if v > 0.01:
      s, y0 = 1.3 * max(v, 0.01), eye_top + 8
      tilt = 0.08 + math.sin(m.clock * 0.9) * 0.04

      def tp(x, y):
        return (x * s * math.cos(tilt) - y * s * math.sin(tilt), y0 + x * s * math.sin(tilt) + y * s * math.cos(tilt))
      outline = [tp(-46, 6), *[tp(x, y) for x, y in self._quad(-46, 6, -30, -42, 20, -46, 8)[1:]],
                 *[tp(x, y) for x, y in self._quad(20, -46, 58, -46, 72, -16, 8)[1:]],
                 *[tp(x, y) for x, y in self._quad(72, -16, 42, -22, 46, 6, 8)[1:]]]
      self._poly(outline, (6, 6, 6, clamp(v, 0, 1)))
      self._stroke(outline + [outline[0]], 4.5, eye, clamp(v, 0, 1))
      px, py = tp(74, -12)
      rl.draw_circle_v(rl.Vector2(px, py), 7 * s, color(eye, clamp(v, 0, 1)))
    self._worn_props(eye, glow, eye_top)

  _above_wheel = None

  def _worn_props(self, eye, glow, eye_top):
    """Props the robot wears or holds, drawn like its sunglasses and nightcap (black, outlined in the eye colour), in
    face space so they move with it: headphones while music plays, a beacon on its head while the hazard lights are on,
    a whistle in its mouth during a driver-monitoring alert, and the seatbelt buckle in its hand when the belt is off."""
    m, P = self.model, self.model.P
    ink = (6, 6, 6)
    # headphones: round cups just outside the eyes (never over them) with a glowing pad that thumps on the beat, and a
    # band that rises straight up from the cups and arches well above the eyes; they slide down on when the music starts
    v = clamp(P['p_phones'].v, 0, 1.1)
    if v > 0.01:
      a, dy = clamp(v, 0, 1), (1 - min(v, 1.0)) * -70
      thump = math.exp(-max(m.clock - m.beat_at, 0.0) * 9)
      cw, ch = 40 * (1 + 0.06 * thump), 54 * (1 + 0.06 * thump)
      rest = GAP + P['w'].v * EYE_SCALE[0] * 0.5
      edge = getattr(self, '_eye_edge', {})
      xl = -(max(edge.get(-1, rest), rest - 30) + cw * 0.5 + 8)    # beside each eye's outer edge, clear of it
      xr = max(edge.get(1, rest), rest - 30) + cw * 0.5 + 8
      cy = EYE_Y + 4 + dy
      top, apex = cy - ch * 0.5 - 2, eye_top - 30 + dy
      k = (apex - 0.25 * top) / 0.75                                # cubic control height that puts its top at apex
      band = [(xl * ((1 - t) ** 3 + 3 * (1 - t) ** 2 * t) + xr * (3 * (1 - t) * t * t + t ** 3),
               top * ((1 - t) ** 3 + t ** 3) + k * (3 * (1 - t) ** 2 * t + 3 * (1 - t) * t * t)) for t in (i / 24 for i in range(25))]
      self._ribbon(band, 14, glow, 0.18 * a)
      self._stroke(band, 6.5, eye, a)
      self._stroke(band[4:-4], 2, ink, 0.8 * a)                     # a groove along it: a padded band, not a wire
      for x in (xl, xr):
        r = rl.Rectangle(x - cw / 2, cy - ch / 2, cw, ch)
        self._blob(x, cy, cw * 2.2, ch * 1.6, glow, (0.2 + 0.25 * thump) * a)
        rl.draw_rectangle_rounded(r, 1.0, 12, color(ink, a))
        rl.draw_rectangle_rounded_lines_ex(r, 1.0, 12, 4, color(eye, a))
        pad = rl.Rectangle(x - cw * 0.27, cy - ch * 0.3, cw * 0.54, ch * 0.6)
        rl.draw_rectangle_rounded(pad, 1.0, 10, color(eye, (0.5 + 0.45 * thump) * a))   # the speaker glow
    # hazard beacon: a red dome on a little base on top of its head, flashing as it turns
    v = clamp(P['p_beacon'].v, 0, 1.15)
    if v > 0.01:
      a, s = clamp(v, 0, 1), max(v, 0.01)
      x, y = 0.0, eye_top - 22
      turn = m.clock * 7.5
      on = 0.5 + 0.5 * math.cos(turn)
      self._blob(x, y - 10 * s, 120 * s, 70 * s, RED_GLOW, 0.45 * on * a)
      for side in (-1, 1):                  # its light sweeping round: a beam to one side, then the other
        k = clamp(math.cos(turn - (0 if side > 0 else math.pi)), 0, 1)
        if k > 0.02:
          self._poly([(x + side * 10 * s, y - 12 * s), (x + side * 86 * s, y - 40 * s), (x + side * 86 * s, y + 10 * s)],
                     (*RED_GLOW, 0.12 * k * a))
          self._poly([(x + side * 10 * s, y - 12 * s), (x + side * 70 * s, y - 24 * s), (x + side * 70 * s, y - 2 * s)],
                     (*RED_GLOW, 0.22 * k * a))
      rl.draw_circle_sector(rl.Vector2(x, y - 4 * s), 15 * s, 180, 360, 20, color(mix(RED_GLOW, (255, 220, 210), 0.4 * on), a))
      rl.draw_circle_v(rl.Vector2(x - 5 * s, y - 11 * s), 3.5 * s, color((255, 240, 235), 0.8 * a))      # its shine
      r = rl.Rectangle(x - 19 * s, y - 5 * s, 38 * s, 10 * s)
      rl.draw_rectangle_rounded(r, 0.4, 6, color(ink, a))
      rl.draw_rectangle_rounded_lines_ex(r, 0.4, 6, 3, color(eye, a))
    # whistle in its mouth, blown in short bursts
    v = clamp(P['p_whistle'].v, 0, 1.15)
    if v > 0.01:
      a, s = clamp(v, 0, 1), 1.2 * max(v, 0.01)
      x = 0.0
      y = self._above_wheel(x, 58.0, 17 * s) if self._above_wheel else 58.0
      blow = 1.0 if math.sin(m.clock * 9) > 0 else 0.0
      mouth = rl.Rectangle(x - 22 * s, y - 5 * s, 22 * s, 10 * s)
      rl.draw_rectangle_rounded(mouth, 0.5, 6, color(ink, a))
      rl.draw_rectangle_rounded_lines_ex(mouth, 0.5, 6, 3, color(eye, a))
      body = rl.Vector2(x + 8 * s, y + 2 * s)
      rl.draw_circle_v(body, 14 * s, color(eye, a))
      rl.draw_circle_v(body, 10.5 * s, color(ink, a))
      rl.draw_circle_v(rl.Vector2(x + 6 * s, y - 6 * s), 3 * s, color(eye, a))                       # the hole on top
      self._stroke(self._quad(x + 21 * s, y + 4 * s, x + 38 * s, y + 4 * s, x + 44 * s, y + 18 * s, 8), 2.5, eye, 0.6 * a)   # cord
      if blow:                              # the sound coming out
        for i, ang in enumerate((-0.5, 0.0, 0.5)):
          r0, r1 = 26 * s, (36 + 4 * (i == 1)) * s
          self._stroke([(body.x + math.cos(ang) * r0, body.y - 4 * s + math.sin(ang) * r0),
                        (body.x + math.cos(ang) * r1, body.y - 4 * s + math.sin(ang) * r1)], 4, eye, a)
    # the seatbelt buckle held up on the driver's side and shaken: "put it on"
    v = clamp(P['p_buckle'].v, 0, 1.15)
    if v > 0.01:
      a, z = clamp(v, 0, 1), 1.35
      hx, hy = -GAP - 78, 30 + (1 - min(v, 1.0)) * 100
      wob = math.sin(m.clock * 15) * 0.22
      strap = [(hx + 2 * z, hy + 8 * z), (hx - 12 * z, hy + 46 * z), (hx - 26 * z, hy + 100 * z)]
      self._ribbon(strap, 13 * z, (96, 98, 110), a)                        # the belt webbing
      self._stroke(strap, 2.5, eye, 0.5 * a)
      dx, dy = math.sin(wob), -math.cos(wob)                               # up from the hand, shaking
      tip = (hx + dx * 26 * z, hy + dy * 26 * z)                           # the metal tongue's middle

      def at(u, w):                                                        # along it u, across it w
        return (tip[0] + dx * u * z - dy * w * z, tip[1] + dy * u * z + dx * w * z)
      plate = [at(-14, -9), at(12, -7), at(14, 0), at(12, 7), at(-14, 9)]
      self._poly(plate, (210, 214, 224, a))
      self._stroke([*plate, plate[0]], 2.5, eye, a)
      slot = [at(2, -4), at(8, -4), at(8, 4), at(2, 4)]
      self._poly(slot, (*ink, a))                                          # its latch hole
      body = mix(eye, glow, BOTTOM_MIX)
      rl.draw_circle_v(rl.Vector2(hx, hy), 18 * z, color(glow, 0.16 * a))
      rl.draw_circle_v(rl.Vector2(hx, hy), 13 * z, color(body, a))
      rl.draw_circle_v(rl.Vector2(hx + 7 * z, hy - 8 * z), 6 * z, color(eye, a))   # the thumb over it

  def _particles(self, eye, glow):
    m = self.model
    for p in m.parts:
      k = p.age / p.life
      if p.kind == 'spark':
        tw = 0.75 + 0.25 * math.sin(p.age * 26 + p.phase)
        r = p.size * (0.5 + math.sin(min(k * 3, 1) * math.pi / 2) * 0.6) * tw
        pts = [(p.x + math.cos(i * math.pi / 4 + k * 3) * (r if i % 2 == 0 else r * 0.28),
                p.y + math.sin(i * math.pi / 4 + k * 3) * (r if i % 2 == 0 else r * 0.28)) for i in range(8)]
        self._poly(pts, (*mix(eye, (255, 255, 255), 0.4), (1 - k) * 0.95))
      elif p.kind == 'sweat':
        a = k / 0.15 if k < 0.15 else 1 - k
        c = rl.Color(150, 215, 255, int(255 * 0.85 * a))
        rl.draw_circle_v(rl.Vector2(p.x, p.y + 2), 7, c)
        self._poly([(p.x, p.y - 12), (p.x + 6.5, p.y), (p.x - 6.5, p.y)], (150, 215, 255, 0.85 * a))
      elif p.kind == 'z':
        a = k / 0.2 if k < 0.2 else (1 - k) / 0.8
        size = p.size * (0.7 + k * 0.7)
        tw = rl.measure_text_ex(self._font, "Z", size, 0)
        rl.draw_text_ex(self._font, "Z", rl.Vector2(p.x + math.sin(p.age * 2 + p.phase) * 6 - tw.x / 2, p.y - tw.y / 2),
                        size, 0, color(eye, 0.85 * a))
      elif p.kind == 'chev':
        if p.age < p.delay:
          continue
        kk = clamp((p.age - p.delay) / (p.life - p.delay), 0, 1)
        c = mix(eye, (255, 190, 90), 0.35)
        d = p.direction
        self._stroke([(p.x - 5 * d, p.y - 10), (p.x + 4 * d, p.y), (p.x - 5 * d, p.y + 10)], 4, c, (1 - kk) * 0.85)
      elif p.kind == 'note':
        a = k / 0.2 if k < 0.2 else 1 - (k - 0.2) / 0.8
        x = p.x + math.sin(p.age * 3 + p.phase) * 6
        c = color(mix(eye, (255, 200, 120), 0.25), a)
        rl.draw_ellipse(int(x), int(p.y), 5.2 * p.size, 4 * p.size, c)
        rl.draw_line_ex(rl.Vector2(x + 4.4 * p.size, p.y - 1.5), rl.Vector2(x + 4.4 * p.size, p.y - 19 * p.size), 2.6, c)
        rl.draw_line_ex(rl.Vector2(x + 4.4 * p.size, p.y - 19 * p.size), rl.Vector2(x + 11 * p.size, p.y - 10 * p.size), 2.6, c)
      elif p.kind == 'heart':
        s = p.size
        pts = []
        for i in range(20):
          t = i / 20 * 2 * math.pi
          pts.append((p.x + math.sin(p.age * 5) * 4 + 16 * math.sin(t) ** 3 / 16 * s,
                      p.y - (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)) / 16 * s))
        self._poly(pts, (255, 120, 155, (1 - k) * 0.9))
      elif p.kind == 'streak':
        # speed lines trailing a lane change
        a = (1 - k) * 0.7
        rl.draw_line_ex(rl.Vector2(p.x, p.y), rl.Vector2(p.x + p.direction * 30 * p.size, p.y), 4, color(eye, a))
      else:                 # "!", puffs, wind, skid marks, beeps
        self._effect(p, k, eye)
    _ = ease_out_cubic

  def _effect(self, p, k, eye):
    """Small effects that go with the robot's own motion, in face space: a startled "!", a "phew" puff, wind past
    it in a bend, tyre marks under hard braking, reversing beeps."""
    x, y, d = p.x, p.y, p.direction
    if p.kind == 'bang':          # "!" by its head: startled
      pop = ease_out_back(k / 0.14) if k < 0.14 else 1.0
      aa = (1 - k) / 0.25 if k > 0.75 else 1.0
      ss = pop * 1.15
      rl.draw_rectangle_rounded(rl.Rectangle(x - 5 * ss, y - 26 * ss, 10 * ss, 32 * ss), 1.0, 6, color(eye, aa))
      rl.draw_circle_v(rl.Vector2(x, y + 16 * ss), 5.5 * ss, color(eye, aa))
    elif p.kind == 'puff':        # "phew"
      r = (5 + k * 7) * p.size
      for ox, oy, rr in ((0, 0, 1.0), (-r * 0.9, r * 0.3, 0.7), (r * 0.8, r * 0.4, 0.75)):
        rl.draw_circle_v(rl.Vector2(x + ox, y + oy), r * rr, color(eye, (1 - k) * 0.8))
    elif p.kind == 'beep':        # reversing beeps
      for j in range(3):
        r = 12 + j * 10 + k * 14
        rl.draw_ring(rl.Vector2(x, y), r - 1.5, r + 1.5, 205, 335, 16, color(eye, (1 - k) * (0.9 - j * 0.25)))
    elif p.kind == 'wind':        # blown toward the outside of the bend
      rl.draw_line_ex(rl.Vector2(x, y), rl.Vector2(x - d * 30, y + 3), 3.5, color(eye, (1 - k) * 0.7))
      rl.draw_line_ex(rl.Vector2(x - d * 30, y + 3), rl.Vector2(x - d * 38, y - 4), 3.5, color(eye, (1 - k) * 0.5))
    elif p.kind == 'skid':        # braking hard: tyre marks
      for side in (-1, 1):
        for j in range(3):
          rl.draw_rectangle_rec(rl.Rectangle(x + side * 160 - 4 - j * side * 16, y - 3, 10, 5), color(eye, (1 - k) * 0.5))
