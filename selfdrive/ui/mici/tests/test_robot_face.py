"""NewUi=3 robot face renderer: car-state inputs and draw calls with a recording raylib stub."""
from collections import namedtuple
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import sys

import pytest

ONROAD = Path(__file__).parents[1] / 'onroad'


class SM(dict):
  def __init__(self):
    super().__init__(
      carState=NS(leftBlinker=False, rightBlinker=False, steeringAngleDeg=0.0, vEgo=10.0, aEgo=0.0, standstill=False,
                  leftBlindspot=False, rightBlindspot=False, gasPressed=False, gearShifter='drive', seatbeltUnlatched=False,
                  steeringPressed=False, vCruiseCluster=0.0, vCruise=0.0),
      selfdriveState=NS(enabled=False, alertType='', alertSound='none'), carControl=NS(latActive=False, actuators=NS(torque=0.0)),
      carOutput=NS(actuatorsOutput=NS(torque=0.0)),
      modelV2=NS(meta=NS(laneChangeState='off', laneChangeDirection='none')),
      radarState=NS(leadOne=NS(status=False, dRel=0.0, vLead=0.0)),
      controlsState=NS(curvature=0.0),
      onroadEvents=[])
    names = ('carState', 'selfdriveState', 'carControl', 'carOutput', 'modelV2', 'radarState', 'controlsState',
             'onroadEvents')
    self.alive = dict.fromkeys(names, True)
    self.valid = dict.fromkeys(names, True)
    self.recv_frame = dict.fromkeys(names, 11)


@pytest.fixture
def face(monkeypatch):
  calls = []
  Color = namedtuple('Color', 'r g b a')
  rl = NS(Color=Color, Rectangle=lambda *a: tuple(a), Vector2=lambda x, y: (x, y), WHITE=Color(255, 255, 255, 255),
          BLACK=Color(0, 0, 0, 255), RL_TRIANGLES=4,
          gen_image_gradient_radial=lambda *a: 'img', load_texture_from_image=lambda img: NS(width=128, height=128),
          measure_text_ex=lambda font, text, size, sp: NS(x=size * 0.6, y=size))
  for name in ('draw_rectangle_rec', 'unload_image', 'draw_texture_pro', 'rl_check_render_batch_limit', 'rl_begin',
               'rl_color4ub', 'rl_vertex2f', 'rl_end', 'draw_line_ex', 'draw_circle_v', 'draw_ellipse',
               'rl_disable_backface_culling', 'rl_enable_backface_culling', 'rl_push_matrix', 'rl_pop_matrix',
               'rl_translatef', 'rl_rotatef', 'rl_scalef', 'draw_rectangle_rounded', 'draw_rectangle_rounded_lines_ex',
               'draw_text_ex', 'draw_triangle_strip', 'draw_ring', 'draw_rectangle_pro', 'draw_circle_sector',
               'draw_rectangle_lines_ex', 'draw_triangle_fan', 'rl_draw_render_batch_active', 'begin_scissor_mode',
               'end_scissor_mode'):
    setattr(rl, name, lambda *a, name=name: calls.append((name, *a)))
  ui = NS(started_frame=10, sm=SM())
  monkeypatch.setitem(sys.modules, 'pyray', rl)
  monkeypatch.setitem(sys.modules, 'openpilot.selfdrive.ui.ui_state', NS(ui_state=ui))
  monkeypatch.setitem(sys.modules, 'openpilot.system.ui.lib.application', NS(gui_app=NS(font=lambda *_: 'font', target_fps=20),
                                                                              FontWeight=NS(BOLD='bold')))
  spec = importlib.util.spec_from_file_location('_test_robot_face', ONROAD / 'robot_face.py')
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  f = module.RobotFace()
  f._audio_chunks = lambda: []           # no microphone in tests
  f._phone.path = '/nonexistent/navdy-feedback.json'   # no Navdy bridge either
  return NS(f=f, ui=ui, calls=calls, rect=NS(x=0, y=0, width=536, height=240))


def names(calls):
  return [c[0] for c in calls]


def test_inputs_read_engagement_alerts_and_motion(face):
  sm = face.ui.sm
  inp = face.f._inputs()
  assert not inp.engaged and not inp.dm_alert and not inp.fcw and inp.v_ego == 10.0
  sm['carControl'].latActive = True                     # Carrot can steer without ACC: still "driving"
  sm['selfdriveState'].alertType = 'driverDistracted2/warning'
  sm['carState'].leftBlinker = True
  sm['carState'].steeringAngleDeg = 30.0                   # wheel turned left
  inp = face.f._inputs()
  assert inp.engaged and inp.dm_alert and inp.left_blinker
  assert inp.steer_deg == -30.0                            # the face looks and leans left
  sm['selfdriveState'].alertType = 'fcw/critical'
  assert face.f._inputs().fcw
  sm.recv_frame['carState'] = 9                          # a previous drive's car state is ignored
  inp = face.f._inputs()
  assert not inp.left_blinker and inp.v_ego == 0.0


def test_inputs_read_driving_events(face):
  sm = face.ui.sm
  inp = face.f._inputs()
  assert (inp.lane_phase, inp.lead_d) == (0, 0.0)
  assert not (inp.cutin or inp.gas or inp.blindspot_left) and inp.steer_torque == 0
  sm['modelV2'].meta = NS(laneChangeState='laneChangeStarting', laneChangeDirection='right')
  sm['radarState'].leadOne = NS(status=True, dRel=9.5, vLead=1.2)
  sm['controlsState'].curvature = 0.02
  sm['onroadEvents'] = [NS(name='steerSaturated')]
  sm['carState'].rightBlindspot = sm['carState'].gasPressed = True
  inp = face.f._inputs()
  assert (inp.lane_phase, inp.lane_dir) == (1, 1)
  assert (inp.lead_d, inp.lead_v) == (9.5, 1.2)
  assert inp.lat_accel == pytest.approx(-0.02 * 10 * 10)        # curvature + is a right bend: lateral + is left
  assert not inp.cutin and inp.blindspot_right and inp.gas
  sm['carState'].leftBlinker = sm['carState'].rightBlinker = True
  sm['carState'].gearShifter, sm['carState'].seatbeltUnlatched, sm['carState'].steeringPressed = 'reverse', True, True
  sm['carState'].vCruiseCluster = 82.0
  inp = face.f._inputs()
  assert inp.hazard and inp.gear == 'reverse'
  assert inp.seatbelt_off and inp.override and inp.set_speed == 82.0
  sm['carState'].leftBlinker = sm['carState'].rightBlinker = False
  sm['carState'].gearShifter, sm['carState'].seatbeltUnlatched, sm['carState'].steeringPressed = 'drive', False, False
  sm['modelV2'].meta = NS(laneChangeState='laneChangeFinishing', laneChangeDirection='left')
  sm['onroadEvents'] = []
  sm['selfdriveState'].alertType = 'radarCutin/warning'   # the one-frame event is gone; its alert is still up
  inp = face.f._inputs()
  assert (inp.lane_phase, inp.lane_dir) == (2, -1) and inp.cutin
  sm['selfdriveState'].alertType = 'belowSteerSpeed/warning'
  assert face.f._inputs().slow_steer
  sm.recv_frame['radarState'] = 9                                 # left over from a previous drive
  assert face.f._inputs().lead_d == 0


def test_navigation_data_does_not_reach_the_face(face):
  # the face reacts to the car and the driver, never to the navigation (cameras, turns, bumps, arrival)
  sm = face.ui.sm
  sm['carrotMan'] = NS(xSpdDist=280, xSpdLimit=80, nGoPosDist=20, xSpdType=22, xTurnInfo=2, xDistToTurn=180)
  sm.alive['carrotMan'] = sm.valid['carrotMan'] = True
  sm.recv_frame['carrotMan'] = 11
  inp = face.f._inputs()
  assert not any(hasattr(inp, k) for k in ('cam_dist', 'cam_limit', 'route_left', 'turn_type', 'turn_dist', 'bump_dist'))


def test_steering_torque_reads_the_sent_torque_as_plus_right(face):
  sm = face.ui.sm
  sm['carOutput'].actuatorsOutput.torque = 0.8                # pushing the wheel left
  assert face.f._inputs().steer_torque == 0                   # only while it is steering
  sm['carControl'].latActive = True
  assert face.f._inputs().steer_torque == pytest.approx(-0.8)
  sm.alive['carOutput'] = False                               # no output: what the controller asked for
  sm['carControl'].actuators.torque = -0.6
  assert face.f._inputs().steer_torque == pytest.approx(0.6)


def test_straining_draws_gritted_teeth_and_shut_eyes(face):
  m = face.f.model
  m.effort, m.effort_side = 1.0, 1
  face.f.draw(face.rect)
  lines = [c for c in face.calls if c[0] == 'draw_line_ex']
  assert len(lines) >= 6 + 2 * 2                              # the zigzag mouth and a >< stroke per eye


def test_listen_feeds_the_microphone_and_dances_on_its_beat(face):
  import time
  import numpy as np
  module_time = time.monotonic
  rate, bpm = 16000, 120
  t = np.arange(int(12 * rate)) / rate
  kick = np.zeros_like(t)
  for b in np.arange(0, 12, 60 / bpm):
    i = int(b * rate)
    tt = t[:int(0.25 * rate)]
    kick[i:i + tt.size] += np.sin(2 * np.pi * (60 + 80 * np.exp(-tt * 12)) * tt)[:kick.size - i] * np.exp(-tt * 10)[:kick.size - i]
  audio = (kick * 12000).astype(np.int16)
  chunks = [audio[i:i + 800] for i in range(0, audio.size, 800)]
  feed = iter(chunks)
  face.f._audio_chunks = lambda: [(next(feed), module_time())]
  beats = 0
  for _ in range(len(chunks)):
    inp = face.f._inputs()
    face.f._listen(0.05, inp)
    beats += len(inp.music_beats)
  assert inp.music_on and abs(60 / inp.music_period - bpm) < 3 and beats >= 8
  face.ui.sm['selfdriveState'].alertSound = 'prompt'       # the car's own alert sound: not music
  face.f._audio_chunks = lambda: [(chunks[0], module_time())]
  inp = face.f._inputs()
  face.f._listen(0.05, inp)
  assert not inp.music_on


def test_phone_playing_dances_without_the_microphone_hearing_a_beat(face, tmp_path, monkeypatch):
  import json
  import time
  clock = [time.monotonic()]

  def tick():                                               # one 20 FPS frame per call (_listen asks once a frame)
    clock[0] += 0.05
    return clock[0]
  monkeypatch.setattr(time, 'monotonic', tick)
  path = tmp_path / 'navdy-feedback.json'
  path.write_text(json.dumps({'receivedAt': time.monotonic(), 'feedback': {'music': {'playing': True}}}))
  face.f._phone.path = str(path)
  beats = 0
  for _ in range(60):                                       # 3 s at 20 FPS, the microphone hears nothing
    inp = face.f._inputs()
    face.f._listen(0.05, inp)
    beats += len(inp.music_beats)
  assert inp.music_on and inp.music_period == 0.5 and beats in (2, 3, 4)   # from 1.5 s in: playing has to hold
  m = face.f.model
  face.ui.sm['carState'].vEgo, face.ui.sm['carState'].standstill = 0.0, True
  face.f._phone.path, face.f._phone.checked = '/nonexistent/navdy-feedback.json', -1e9
  for _ in range(int(60 / 0.05)):                           # parked a minute without music: it dozes off
    inp = face.f._inputs()
    face.f._listen(0.05, inp)
    m.update(0.05, inp)
  assert m.sleep_stage == 2 and not inp.music_on
  path.write_text(json.dumps({'receivedAt': time.monotonic(), 'feedback': {'music': {'playing': True}}}))
  face.f._phone.path, face.f._phone.checked = str(path), -1e9
  for _ in range(int(5 / 0.05)):                            # the phone starts a song: it wakes up and dances
    inp = face.f._inputs()
    face.f._listen(0.05, inp)
    m.update(0.05, inp)
  assert m.sleep_stage == 0 and m.mood == 'vibe' and m.beats >= 4
  path.write_text(json.dumps({'receivedAt': time.monotonic(), 'feedback': {'music': {'playing': False}}}))
  face.f._phone.mtime, face.f._phone.checked = None, -1e9
  inp = face.f._inputs()
  face.f._listen(0.05, inp)
  assert not inp.music_on                                   # paused


def test_event_props_draw(face):
  m = face.f.model
  m.spawn(kind='streak', x=-150, y=0, life=0.5, direction=-1)
  m.spawn(kind='bang', x=196, y=-84, life=1.4, age=0.4)
  m.spawn(kind='wind', x=-150, y=-40, life=0.7, direction=1)
  face.f.draw(face.rect)
  n = names(face.calls)
  assert n.count('draw_circle_v') >= 1                    # the dot of the "!"
  assert 'draw_line_ex' in n and 'draw_rectangle_rounded' in n


def test_render_draws_both_eyes_and_keeps_the_matrix_balanced(face):
  face.f.render(face.rect)                                # a new drive: the hello starts with the eyes shut
  for _ in range(60):
    face.f.model.update(0.05, face.f._inputs())
  face.calls.clear()
  face.f.render(face.rect)
  n = names(face.calls)
  assert n[0] == 'draw_rectangle_rec'
  assert n.count('rl_push_matrix') == n.count('rl_pop_matrix') == 1   # the face
  strips = [c for c in face.calls if c[0] == 'draw_triangle_strip']
  assert len(strips) >= 8                                 # both eye bodies, filled in gradient bands
  assert len({c[3] for c in strips}) >= 4                 # the bands step through colours
  assert n.count('rl_vertex2f') == 0                      # no per-vertex calls for the eyes
  layers = type(face.f).__init__.__globals__['GLOW_LAYERS']
  assert sum(1 for c in strips if c[3].a < 60) >= 2 * layers   # a halo around each eye, following its outline


@pytest.mark.parametrize('shape', ['heart', 'star'])
def test_special_eyes(face, shape):
  m = face.f.model
  if shape == 'heart':
    m.eye_shape = 'heart'
  else:
    m.stars(0.5)
  face.f.draw(face.rect)
  fans = [c for c in face.calls if c[0] == 'draw_triangle_fan']
  assert len(fans) == 2 * 4                                  # each eye: three halo steps and the shape itself
  assert all(c[2] >= 12 for c in fans)                       # centre + outline + closing point
  # through raylib's fan (it binds the shapes texture): raw rlBegin/rlVertex drew nothing on the device GPU
  assert names(face.calls).count('rl_vertex2f') == 0


def test_shut_eyes_draw_a_stroke_and_props_draw(face):
  m = face.f.model
  m.P['up'].v = 1.0
  for k in ('p_shades', 'p_quest', 'p_bulb', 'p_nightcap'):
    m.P[k].v = 1.0
  m.spawn(kind='z', x=150, y=-40, life=2, size=14)
  m.spawn(kind='bang', x=196, y=-84, life=1.4)
  m.spawn(kind='sweat', x=-186, y=-64, life=2)
  m.spawn(kind='note', x=120, y=-50, life=1.6)
  m.spawn(kind='heart', x=0, y=-20, life=1.4, size=8)
  m.spawn(kind='spark', x=100, y=-40, life=1, size=6)
  m.spawn(kind='chev', x=206, y=-8, life=0.5)
  face.f.draw(face.rect)
  n = names(face.calls)
  assert n.count('draw_line_ex') > 10
  assert 'draw_rectangle_rounded' in n and 'draw_rectangle_rounded_lines_ex' in n
  texts = [c[2] for c in face.calls if c[0] == 'draw_text_ex']
  assert '?' in texts and 'Z' in texts


def test_wheel_shows_while_driving_and_the_mouth_stays_above_it(face):
  import math
  sm, m = face.ui.sm, face.f.model
  sm['selfdriveState'].enabled = True
  for _ in range(80):                                      # engaged long enough for the wheel to come up
    m.update(0.05, face.f._inputs())
  m.P['mO'].v = m.P['mO'].t = 1.0                          # mouth wide open (surprise, a yawn)
  m.P['hy'].v = 40.0                                       # and the head pushed well down (a crouch)
  face.calls.clear()
  face.f.draw(face.rect)
  rings = [c for c in face.calls if c[0] == 'draw_ring']
  assert len(rings) >= 5                                   # the rim, its edges and halo
  g = type(face.f).__init__.__globals__
  top = face.rect.height + g['WHEEL_DROP'] - g['WHEEL_R'] - g['WHEEL_RIM']
  ellipses = [c for c in face.calls if c[0] == 'draw_ellipse']
  mouth = max(ellipses, key=lambda c: c[4])                # the open mouth: the tallest ellipse
  # draw() runs inside the face's transform; undo it for the mouth's lowest point on screen
  hx, hy = face.rect.width / 2, face.rect.height / 2 + 40.0
  assert hy + mouth[2] + mouth[4] <= top + 8               # (the transform's scale and tilt are close to 1 and 0)
  _ = math, hx


def test_set_speed_is_drawn_digit_by_digit(face):
  m = face.f.model
  m.spawn(kind='speed', x=0, y=0, life=2.0, value=85, direction=0, age=1.0)
  face.calls.clear()
  face.f.draw(face.rect)
  texts = [c[2] for c in face.calls if c[0] == 'draw_text_ex']
  assert texts.count('8') == 1 and texts.count('5') == 1


def test_no_event_icons_and_no_driver_monitoring_eye(face):
  # the robot shows events with its own face and motion (and the wheel it holds): no icons by the screen edges, and no
  # blinking eye for driver monitoring
  m = face.f.model
  for name in ('brake', 'bsm', 'lead_go', 'cutin', 'gas', 'slow_steer', 'lane_go', 'lane_done', 'hello', 'seatbelt',
               'reverse', 'park', 'hazard', 'dm'):
    m.react(name, 1)
  for _ in range(10):
    m.update(0.05, face.f._inputs())
  assert {p.kind for p in m.parts} <= {'bang', 'spark', 'heart', 'sweat', 'puff', 'skid', 'beep', 'wind', 'streak', 'note', 'z', 'chev'}
  counts = []
  for dm in (False, True):                                # a driver-monitoring alert draws nothing extra
    m.dm = dm
    face.calls.clear()
    face.f.draw(face.rect)
    n = names(face.calls)
    counts.append((n.count('draw_line_ex'), n.count('draw_circle_v'), n.count('draw_rectangle_rounded')))
  assert counts[0] == counts[1]


def test_set_speed_digits_roll_the_way_it_went(face):
  m = face.f.model
  m.spawn(kind='speed', x=0, y=0, life=2.4, value=85, prev=80, direction=1, age=0.23)
  face.calls.clear()
  face.f.draw(face.rect)
  at = {c[2]: c[3][1] for c in face.calls if c[0] == 'draw_text_ex'}     # digit: its top on screen
  assert at['0'] < at['8'] < at['5']                      # going up: the old 0 rises out, the new 5 comes up from below
  assert any(c[0] == 'draw_triangle_strip' for c in face.calls)          # the chevrons
  assert any(c[0] == 'begin_scissor_mode' for c in face.calls)           # rolling inside the number's own window
  m.parts.clear()
  m.spawn(kind='speed', x=0, y=0, life=2.4, value=80, prev=85, direction=-1, age=0.23)
  face.calls.clear()
  face.f.draw(face.rect)
  at = {c[2]: c[3][1] for c in face.calls if c[0] == 'draw_text_ex'}
  assert at['5'] > at['8'] > at['0']                      # going down: the reverse


def test_fans_wind_counter_clockwise_and_the_batch_flushes_before_culling(face):
  # clockwise outlines (most here) were dropped by the GPU once culling was back on when the batch was drawn
  face.f._poly([(0, -10), (10, 0), (0, 10), (-10, 0)], (255, 255, 255, 1.0))      # clockwise on screen
  fan = [c for c in face.calls if c[0] == 'draw_triangle_fan'][-1][1]
  outline = [(v[0], v[1]) for v in fan[1:-1]]
  area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(outline, [*outline[1:], outline[0]], strict=True))
  assert area < 0                                           # counter-clockwise on screen, raylib's front face
  face.calls.clear()
  face.f.draw(face.rect)
  n = names(face.calls)
  assert n.index('rl_draw_render_batch_active') < n.index('rl_enable_backface_culling')
  assert n[-2:] == ['rl_draw_render_batch_active', 'rl_enable_backface_culling']


def test_the_eyes_shut_before_the_set_speed_shows_and_open_after_it(face):
  m = face.f.model

  def frame(age):
    m.parts[:] = [p for p in m.parts if p.kind != 'speed']
    m.spawn(kind='speed', x=0, y=0, life=2.4, value=85, direction=0, age=age)
    face.calls.clear()
    face.f.draw(face.rect)
    texts = [c[2] for c in face.calls if c[0] == 'draw_text_ex']
    hidden = ('rl_scalef', 0.0, 0.0, 1) in face.calls
    return texts, hidden

  texts, hidden = frame(0.1)                              # shutting: no number yet, the face still drawn
  assert not texts and not hidden
  texts, hidden = frame(1.0)                              # shut: the number, and nothing of the face at all
  assert '8' in texts and hidden
  texts, hidden = frame(2.3)                              # the number gone: the eyes open again
  assert not texts and not hidden


def test_worn_props_draw_and_the_buckle_takes_the_left_hand_off_the_wheel(face):
  m = face.f.model
  face.ui.sm['selfdriveState'].enabled = True
  for _ in range(40):                                     # driving: the wheel is up with both hands on it
    m.update(0.05, face.f._inputs())
  face.calls.clear()
  face.f.draw(face.rect)
  hands = len([c for c in face.calls if c[0] == 'draw_circle_v' and c[2] == 13])
  for k in ('p_phones', 'p_beacon', 'p_whistle'):
    m.P[k].v = 1.0
  m.P['p_buckle'].v = 1.0
  face.calls.clear()
  face.f.draw(face.rect)
  n = names(face.calls)
  assert n.count('draw_rectangle_rounded_lines_ex') >= 3   # the two headphone cups and the beacon's base
  assert n.count('draw_circle_sector') >= 1                # the beacon's dome
  wheel_hands = len([c for c in face.calls if c[0] == 'draw_circle_v' and c[2] == 13])
  assert wheel_hands == hands - 1                          # one wheel hand is now the one holding the buckle


def test_small_steering_angles_show_bigger_and_the_hands_stay_on_screen(face):
  m = face.f.model
  face.ui.sm['selfdriveState'].enabled = True
  for _ in range(40):
    m.update(0.05, face.f._inputs())
  g = type(face.f).__init__.__globals__

  def drawn(steer):
    m.P['steer'].v = steer
    face.calls.clear()
    face.f.draw(face.rect)
    stripe = [c for c in face.calls if c[0] == 'draw_rectangle_pro' and c[1][2] == 9][0]   # the 12 o'clock stripe
    hands = [c for c in face.calls if c[0] == 'draw_circle_v' and c[2] == g['HAND_R']]
    return stripe[3], hands

  turn, _ = drawn(1.0)
  assert turn == pytest.approx(3.0, abs=0.15)              # lane keeping: 1 deg reads as about 3
  turn, _ = drawn(-2.0)
  assert -6.0 < turn < -5.0
  for steer in (8.0, 40.0, 90.0, -90.0):                   # a bend or a turn: only as far as the hands reach
    turn, hands = drawn(steer)
    assert abs(turn) <= g['WHEEL_TURN_MAX'] + 1e-6
    assert len(hands) == 2 and all(h[1][1] + g['HAND_R'] <= face.rect.height + 0.5 for h in hands)
  assert g['WHEEL_TURN_MAX'] == pytest.approx(9.4, abs=0.1)
