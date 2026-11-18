"""NewUi=3 robot face model: eye geometry, car-state reactions, habits. Pure Python, no drawing."""
import math
import random

import pytest

from openpilot.selfdrive.ui.mici.onroad.robot_face_model import FaceInputs, FaceModel, eye_spans


def run(m, inp, sec, dt=0.05, each=None):
  for _ in range(int(round(sec / dt))):
    if each:
      each()
    m.update(dt, inp)


@pytest.fixture
def m():
  model = FaceModel(random.Random(7))
  model.update(0.05, FaceInputs(session=1))
  return model


def test_open_eye_fills_the_outline_and_a_shut_one_is_empty():
  spans = eye_spans(0, 0, 90, 100, 3.2, 0.0, 0.0, 0.0, 0.0, 0.0, -1)
  assert len(spans) >= 30
  mid = min(spans, key=lambda s: abs(s[0]))
  assert mid[1] == pytest.approx(-50, abs=0.5) and mid[2] == pytest.approx(50, abs=0.5)
  assert not any(s[3] for s in spans)                       # nothing cut by a lid
  assert eye_spans(0, 0, 90, 100, 3.2, 1.0, 0.0, 0.0, 0.0, 0.0, -1) == []


def test_happy_eye_is_a_crescent_and_tilt_slopes_the_lid():
  spans = eye_spans(0, 0, 90, 100, 3.2, 0.0, 0.46, 0.0, 1.0, 0.0, 1)
  mid = min(spans, key=lambda s: abs(s[0]))
  edge = spans[2]
  assert mid[1] == pytest.approx(-50, abs=0.5)
  assert mid[2] < -10 and edge[2] > mid[2]                  # the lower lid bulges up in the middle
  worried = eye_spans(0, 0, 90, 100, 3.2, 0.3, 0.0, -0.7, 0.0, 0.0, -1)
  # left eye, worried: the outer (left) side is cut lower than the inner (right) side
  assert worried[3][1] > worried[-4][1]


def test_engage_hops_with_star_eyes_then_drives_green(m):
  inp = FaceInputs(session=1, engaged=True)
  run(m, inp, 0.3)
  assert m.mood == 'happy' and m.clock < m.star_until
  run(m, inp, 1.6)
  assert m.mood == 'focus' and m.P['green'].t == pytest.approx(0.9)
  inp.engaged = False
  run(m, inp, 0.2)
  assert m.mood == 'sigh'
  run(m, inp, 1.3)
  assert m.mood == 'idle' and m.P['green'].t == 0


@pytest.mark.parametrize('left,right,sign', [(True, False, -1), (False, True, 1), (True, True, 0)])
def test_turn_signal_looks_that_way(m, left, right, sign):
  inp = FaceInputs(session=1, left_blinker=left, right_blinker=right)
  run(m, inp, 1.0)
  assert m.signal == sign
  if sign:
    assert math.copysign(1, m.P['gxL'].v) == sign and abs(m.P['gxL'].v) > 0.4
    assert any(p.kind == 'chev' for p in m.parts) or m.tick_phase > 0


def test_driver_monitoring_alert_worries_until_cleared(m):
  inp = FaceInputs(session=1, dm_alert=True)
  run(m, inp, 1.0)
  assert m.mood == 'worried' and m.tremble == 1.0 and m.P['amber'].t == 1
  inp.dm_alert = False
  run(m, inp, 0.2)
  assert m.mood == 'sigh' and m.tremble == 0
  run(m, inp, 1.0)
  assert m.mood == 'idle'


def test_hard_braking_startles_once_per_cooldown(m):
  inp = FaceInputs(session=1, a_ego=-4.0, v_ego=10)
  run(m, inp, 0.15)
  assert m.mood == 'surprised' and any(p.kind == 'bang' for p in m.parts)
  first_until = m.brake_until
  run(m, inp, 1.0)
  assert m.brake_until == first_until                       # still braking, no new startle inside 3 s
  inp.a_ego, inp.fcw = 0.0, True
  run(m, inp, 2.5)
  assert m.brake_until > first_until                        # a forward collision warning also startles


def test_long_stop_dozes_off_and_pulling_away_wakes_it(m):
  inp = FaceInputs(session=1, standstill=True)
  run(m, inp, 44.0)
  assert m.sleep_stage == 0
  run(m, inp, 1.5)
  assert m.sleep_stage == 1 and m.mood == 'drowsy'
  run(m, inp, 8.0)
  assert m.sleep_stage == 2 and m.mood == 'asleep' and m.P['p_nightcap'].v > 0.5
  assert any(p.kind == 'z' for p in m.parts)
  inp.standstill, inp.v_ego = False, 2.0
  run(m, inp, 0.1)
  assert m.sleep_stage == 0 and m.mood == 'surprised'
  run(m, inp, 3.0)
  assert m.mood == 'idle'


def test_new_session_resets(m):
  run(m, FaceInputs(session=1, engaged=True, dm_alert=True), 2.0)
  run(m, FaceInputs(session=2), 0.1)
  assert m.mood == 'idle' and not m.dm and m.P['bright'].t == 1.0   # no night dimming: full colour day and night


def test_every_habit_runs_and_nothing_goes_wild():
  model = FaceModel(random.Random(3))
  names = list(model._behaviors())
  for engaged in (False, True):
    for name in names:
      model.react('engage' if engaged else 'disengage')
      model.update(0.05, FaceInputs(session=1, engaged=engaged))
      run(model, FaceInputs(session=1, engaged=engaged), 2.5)
      model.run_behavior(name)
      run(model, FaceInputs(session=1, engaged=engaged), 4.0)
      for k, s in model.P.items():
        assert math.isfinite(s.v) and abs(s.v) < 2000, (name, k, s.v)
  assert model.behavior is None or model.behavior in names


def test_long_random_drive_picks_many_habits_without_errors():
  model = FaceModel(random.Random(11))
  inp = FaceInputs(session=1)
  seen = set()
  for i in range(int(900 / 0.05)):
    t = i * 0.05
    inp.engaged = 300 < t < 600
    inp.right_blinker = 450 < t < 455
    inp.steer_deg = 25 * math.sin(t / 3)
    inp.v_ego = 15.0
    model.update(0.05, inp)
    if model.behavior:
      seen.add(model.behavior)
  assert len(seen) >= 15
  assert all(math.isfinite(s.v) for s in model.P.values())


# ---- driving-event reactions -------------------------------------------------------------------
def ready(engaged=False, **kw):
  """A model past its session-start hello (and engage hop), plus the inputs it is running on."""
  model = FaceModel(random.Random(4))
  inp = FaceInputs(session=1, engaged=engaged, v_ego=kw.pop('v_ego', 15.0), **kw)
  run(model, inp, 4.0)
  model.last_event = None
  return model, inp


def test_session_start_says_hello():
  model = FaceModel(random.Random(1))
  model.update(0.05, FaceInputs(session=1))
  assert model.last_event == 'hello' and model.P['up'].t == 1.0          # eyes start shut
  run(model, FaceInputs(session=1), 1.0)
  assert model.mood == 'happy'
  run(model, FaceInputs(session=1), 1.0)
  assert model.mood == 'idle' and model.P['up'].t == 0


def test_lane_change_leans_in_then_nods_only_when_it_finishes():
  m, inp = ready(engaged=True)
  inp.right_blinker = True
  run(m, inp, 0.5)
  inp.lane_phase, inp.lane_dir = 1, 1
  run(m, inp, 0.1)
  assert m.last_event == 'lane_go' and m.P['ex'].t > 0 and any(p.kind == 'streak' for p in m.parts)
  run(m, inp, 2.0)
  inp.lane_phase = 2
  run(m, inp, 0.5)
  inp.lane_phase, inp.right_blinker = 0, False
  run(m, inp, 0.1)
  assert m.last_event == 'lane_done' and m.mood == 'happy'
  run(m, inp, 3.0)
  assert m.mood == 'focus'
  inp.lane_phase, inp.lane_dir = 1, -1                    # called off before it finished: no nod
  run(m, inp, 2.0)
  inp.lane_phase = 0
  run(m, inp, 0.2)
  assert m.last_event == 'lane_go'


def test_blind_spot_car_on_the_signalled_side_startles():
  m, inp = ready(engaged=True)
  inp.blindspot_right = True                              # not signalling toward it: nothing
  run(m, inp, 0.5)
  assert m.last_event is None
  inp.right_blinker = True
  run(m, inp, 0.1)
  assert m.last_event == 'bsm' and m.P['ex'].t < 0 and m.P['amber'].t > 0
  assert any(p.kind == 'bang' and p.x > 0 for p in m.parts)
  m.last_event = None
  inp.blindspot_right = False
  run(m, inp, 0.5)
  inp.blindspot_right = True                              # back again inside the cooldown
  run(m, inp, 0.5)
  assert m.last_event is None


def test_car_ahead_pulling_away_while_stopped_even_from_sleep():
  m, inp = ready(v_ego=0.0, standstill=True, lead_d=8.0)
  inp.lead_v = 2.0
  run(m, inp, 0.3)
  assert m.last_event is None                             # has to keep moving for 0.4 s
  run(m, inp, 0.2)
  assert m.last_event == 'lead_go' and m.P['h'].t > 130
  m, inp = ready(v_ego=0.0, standstill=True, lead_d=8.0)
  run(m, inp, 55.0)
  assert m.sleep_stage == 2
  inp.lead_v = 1.6
  run(m, inp, 0.5)
  assert m.last_event == 'lead_go' and m.sleep_stage == 0
  run(m, inp, 2.0)
  assert m.sleep_stage == 0                               # still stopped, but it does not doze straight off again


def test_straining_grows_with_steering_torque():
  m, inp = ready(engaged=True)
  efforts = []
  for torque in (0.3, 0.5, 0.65, 0.8, 0.95, 1.0):
    inp.steer_torque = -torque                             # pushing left
    run(m, inp, 1.0)
    efforts.append(m.effort)
  assert efforts[0] == 0 and efforts[1] < 0.05             # everyday torque: no strain
  assert efforts == sorted(efforts) and efforts[-1] > 0.95  # harder and harder, all-out at the limit
  assert m.effort_side == -1 and m.behavior is None
  inp.steer_torque = 0.1
  run(m, inp, 0.3)
  assert 0.2 < m.effort < 0.95                             # eases off a little slower than it builds
  run(m, inp, 3.0)
  assert m.effort < 0.01
  m, inp = ready(engaged=False, steer_torque=1.0)          # not steering it: no strain
  run(m, inp, 1.0)
  assert m.effort == 0


def test_cut_in_and_gas_override():
  m, inp = ready(engaged=True)
  inp.cutin = True
  run(m, inp, 0.1)
  assert m.last_event == 'cutin'
  run(m, inp, 2.0)
  inp.gas = True
  run(m, inp, 0.1)
  assert m.last_event == 'gas' and m.P['p_quest'].t == 1
  m, inp = ready(engaged=False)                           # not driving it: no gas glance
  inp.gas = True
  run(m, inp, 0.5)
  assert m.last_event is None


def test_gas_glance_never_hides_a_lane_change_or_a_bend():
  # replayed drives: a gas press just before a lane change and a fast bend took both reactions away
  m, inp = ready(engaged=True)
  inp.gas = True
  run(m, inp, 0.3)
  assert m.last_event == 'gas'
  inp.right_blinker = True
  run(m, inp, 0.3)
  inp.lane_phase, inp.lane_dir = 1, 1
  run(m, inp, 0.1)
  assert m.last_event == 'lane_go' and m.P['asym'].t == 0   # none of the curious glance's lopsided eyes left over
  m, inp = ready(engaged=True)
  inp.gas = True
  run(m, inp, 0.2)
  inp.right_blinker = True                                # gas while signalling is just the lane change: no glance
  m.last_event = None
  inp.gas = False
  run(m, inp, 0.2)
  inp.gas = True
  run(m, inp, 0.3)
  assert m.last_event is None
  m, inp = ready(engaged=True)
  inp.cutin = True
  run(m, inp, 0.1)
  inp.lat_accel = 3.0                                     # the bend starts during the cut-in flinch
  run(m, inp, 1.0)
  assert m.last_event == 'cutin'
  run(m, inp, 0.6)
  assert m.last_event == 'curve'                          # queued, then played


def test_too_slow_to_steer_hands_over_then_takes_back():
  m, inp = ready(engaged=True, v_ego=2.5)
  m.behavior_timer = 1e9                                   # no idle habit in between
  inp.slow_steer = True
  run(m, inp, 0.1)
  assert m.last_event == 'slow_steer' and m.P['green'].t == FaceModel.SLOW_GREEN
  run(m, inp, 3.0)
  assert m.mood == 'focus' and m.P['green'].t == FaceModel.SLOW_GREEN   # stays pale while it cannot steer
  inp.slow_steer, inp.v_ego = False, 3.5
  run(m, inp, 0.1)
  assert m.last_event == 'steer_back'
  run(m, inp, 1.5)
  assert m.mood == 'focus' and m.P['green'].t == pytest.approx(0.9)
  m.last_event = None
  inp.slow_steer = True                                    # stop-and-go: not again inside 20 s
  run(m, inp, 1.0)
  inp.slow_steer = False
  run(m, inp, 1.0)
  assert m.last_event is None                              # and no "got it" without the hand-over
  m, inp = ready(engaged=False, v_ego=2.5)                 # not driving it: nothing to hand over
  inp.slow_steer = True
  run(m, inp, 0.5)
  assert m.last_event is None


def test_dances_to_music_only_when_nothing_else_is_going_on():
  m, inp = ready(engaged=True)
  inp.music_on, inp.music_period = True, 0.5
  y0, notes = m.P['hy'].vel, 0
  for i in range(80):                                     # 4 s at 120 BPM
    inp.music_beats = (0.8,) if i % 10 == 0 else ()
    m.update(0.05, inp)
    notes += sum(p.kind == 'note' and p.age < 0.06 for p in m.parts)
  assert m.mood == 'vibe' and m.beats == 8 and notes >= 2
  assert m.P['green'].t == 0.6                              # still the driving tint while it dances
  inp.right_blinker = True                                # a turn signal: no dancing through it
  beats = m.beats
  for i in range(20):
    inp.music_beats = (0.8,) if i % 10 == 0 else ()
    m.update(0.05, inp)
  assert m.beats == beats
  inp.right_blinker, inp.music_on, inp.music_beats = False, False, ()
  m.behavior_timer = 1e9                                  # no idle habit in between
  run(m, inp, 3.5)
  assert m.mood == 'focus'                                # the music stopped: back to driving


def test_music_wakes_it_from_a_long_stop_and_keeps_it_up():
  # parked and asleep, as when it was tried in the car: the music has to wake it, or it never dances
  m, inp = ready(engaged=True, v_ego=0.0, standstill=True)
  run(m, inp, 60.0)
  assert m.sleep_stage == 2
  inp.music_on, inp.music_period = True, 0.5
  for i in range(int(60 / 0.05)):                         # a minute of music, still parked
    inp.music_beats = (0.8,) if i % 10 == 0 else ()
    m.update(0.05, inp)
  assert m.sleep_stage == 0 and m.mood == 'vibe' and m.beats > 100


def test_cut_in_at_crawling_speed():
  m, inp = ready(engaged=True, v_ego=2.8)                 # 10 km/h, as in the replayed drive
  inp.cutin = True
  run(m, inp, 0.1)
  assert m.last_event == 'cutin'


def test_driver_alert_and_hard_braking_outrank_driving_events():
  m, inp = ready(engaged=True)
  inp.dm_alert = True
  run(m, inp, 0.5)
  inp.lane_phase, inp.lane_dir = 1, 1
  run(m, inp, 0.5)
  assert m.last_event == 'dm' and m.mood == 'worried'
  inp.dm_alert, inp.lane_phase = False, 0
  run(m, inp, 3.0)
  inp.a_ego = -4.5
  run(m, inp, 0.1)
  inp.a_ego, inp.cutin = 0.0, True
  run(m, inp, 0.5)
  assert m.last_event == 'brake'


def test_fast_bend_has_to_last():
  m, inp = ready(engaged=True)
  inp.lat_accel = 3.0
  run(m, inp, 0.5)
  assert m.last_event is None
  run(m, inp, 0.5)
  assert m.last_event == 'curve' and m.P['ex'].t > 0        # a left bend swings it to the right
  m, inp = ready(engaged=True, v_ego=5.0, lat_accel=3.0)  # slow: just a corner
  run(m, inp, 2.0)
  assert m.last_event is None


@pytest.mark.parametrize('engaged', [False, True])
def test_every_driving_reaction_settles_back(engaged):
  names = [n for n in FaceModel.PRIO if n not in ('brake', 'dm', 'engage', 'disengage', 'dm_clear', 'go', 'sleepy')]
  m, inp = ready(engaged=engaged)
  for name in names:
    m.behavior_timer = 1e9                                # no idle habit afterwards, so the resting face shows
    m.react(name, -1)
    run(m, inp, 3.5)
    assert m.mood == m.rest_mood() and not m.squeeze and m.tremble == 0, name
    assert m.P['ex'].t == 0 and m.P['p_quest'].t == 0, name
    assert all(math.isfinite(s.v) for s in m.P.values()), name


def icons(m, kind):
  return [p for p in m.parts if p.kind == kind]


def test_seatbelt_off_reminds_every_half_minute():
  m, inp = ready(engaged=True)
  inp.seatbelt_off = True
  run(m, inp, 0.1)
  assert m.last_event == 'seatbelt' and m.mood == 'worried'
  m.last_event = None
  run(m, inp, 20.0)
  assert m.last_event is None
  run(m, inp, 11.0)
  assert m.last_event == 'seatbelt'


def test_reverse_and_park_react():
  m, inp = ready(gear='drive', v_ego=0.0, standstill=True)
  inp.gear = 'reverse'
  run(m, inp, 0.4)
  assert m.last_event == 'reverse' and any(p.kind == 'beep' for p in m.parts)      # looking back, beeping
  run(m, inp, 3.0)
  inp.gear = 'park'
  run(m, inp, 0.1)
  assert m.last_event == 'park'


def test_hazard_lights_worry_it():
  m, inp = ready()
  inp.hazard = inp.left_blinker = inp.right_blinker = True
  run(m, inp, 0.1)
  assert m.last_event == 'hazard' and m.mood == 'worried' and m.signal == 0      # not a turn signal


def test_set_speed_rolls_in_on_engagement_and_rolls_over_on_a_change():
  m, inp = ready(engaged=True, set_speed=80.0)
  assert [(p.value, p.prev, p.direction) for p in icons(m, 'speed')] == []          # shown on engaging, gone by now
  m, inp = ready(set_speed=80.0)
  inp.engaged = True
  run(m, inp, 0.1)
  (intro,) = icons(m, 'speed')
  assert (intro.value, intro.prev, intro.direction) == (80.0, 0.0, 0)                 # engaging: it drops in
  assert intro.delay == m.SPEED_AFTER_ENGAGE_S                                       # after the star-eyed hop
  run(m, inp, 4.0)
  assert not icons(m, 'speed')
  inp.set_speed = 85.0
  run(m, inp, 0.1)
  assert [(p.value, p.prev, p.direction) for p in icons(m, 'speed')] == [(85.0, 80.0, 1)]   # rolls over, up
  age = icons(m, 'speed')[0].age
  inp.set_speed = 82.0
  run(m, inp, 0.1)
  (shown,) = icons(m, 'speed')                                                       # the same one, rolling on
  assert (shown.value, shown.prev, shown.direction) == (82.0, 85.0, -1) and shown.since >= age > 0
  assert shown.life - shown.age == pytest.approx(m.SPEED_SHOW_S - 0.05, abs=0.06)
  run(m, inp, 2.5)
  assert not icons(m, 'speed')
  inp.engaged = False                                                                # off and on again: shown again
  run(m, inp, 1.0)
  assert not icons(m, 'speed')
  inp.engaged = True
  run(m, inp, 0.1)
  assert [(p.value, p.direction) for p in icons(m, 'speed')] == [(82.0, 0)]


def test_set_speed_makes_way_for_reactions_that_need_the_eyes():
  m, inp = ready(engaged=True, set_speed=80.0)
  inp.set_speed = 85.0
  run(m, inp, 0.5)
  assert icons(m, 'speed')
  inp.a_ego = -4.5                                                                   # hard braking
  run(m, inp, 0.4)
  assert not icons(m, 'speed') and m.last_event == 'brake'
  m, inp = ready(set_speed=80.0)
  inp.engaged = True
  run(m, inp, 0.3)                                                                   # still waiting for the hop
  inp.dm_alert = True
  run(m, inp, 0.05)
  assert not icons(m, 'speed')


def test_set_speed_waits_for_a_valid_speed_after_engaging():
  m, inp = ready(set_speed=255.0)                                                    # unset on this car
  inp.engaged = True
  run(m, inp, 1.0)
  assert not icons(m, 'speed')
  inp.set_speed = 70.0
  run(m, inp, 0.1)
  assert [(p.value, p.direction) for p in icons(m, 'speed')] == [(70.0, 0)]


def test_wheel_rises_when_driving_and_its_hands_let_go_for_the_driver():
  m, inp = ready()
  assert m.P['drive'].v < 0.05
  inp.engaged = True
  run(m, inp, 3.0)
  assert m.P['drive'].v > 0.95 and m.P['hands'].v > 0.95
  inp.override = True                                     # the driver turns the wheel
  run(m, inp, 0.5)
  assert m.last_event == 'override' and m.P['hands'].v < 0.3
  inp.override = False
  run(m, inp, 2.0)
  assert m.P['hands'].v > 0.9
  inp.slow_steer = True                                   # too slow to steer: hands off until it can again
  run(m, inp, 1.0)
  assert m.last_event == 'slow_steer'
  run(m, inp, 1.5)
  assert m.P['hands'].v < 0.1
  inp.slow_steer = False
  run(m, inp, 1.5)
  assert m.P['hands'].v > 0.9
  inp.engaged = False
  run(m, inp, 3.0)
  assert m.P['drive'].v < 0.05


def test_driver_steering_during_a_lane_change_is_not_a_takeover():
  m, inp = ready(engaged=True)
  inp.lane_phase, inp.lane_dir, inp.override = 1, 1, True
  run(m, inp, 1.0)
  assert m.last_event == 'lane_go'


def test_reactions_spawn_no_icons():
  icon_kinds = {'arrow', 'badge', 'belt', 'car', 'check', 'gauge', 'hazard', 'turtle', 'wave', 'cam', 'sign', 'flag'}
  for name in ('brake', 'bsm', 'lead_go', 'cutin', 'gas', 'slow_steer', 'lane_go', 'lane_done', 'hello', 'seatbelt',
               'reverse', 'park', 'hazard', 'override', 'curve', 'go', 'steer_back'):
    m, inp = ready(engaged=name in ('gas', 'slow_steer', 'lane_go', 'lane_done', 'cutin', 'bsm', 'override', 'curve'))
    m.react(name, 1)
    run(m, inp, 1.0)
    assert not {p.kind for p in m.parts} & icon_kinds, name
  m, inp = ready(engaged=True)                                                        # the "!" is back by its head
  m.react('brake')
  run(m, inp, 0.1)
  assert [(p.x, p.y) for p in m.parts if p.kind == 'bang'] == [(196, -84)]


def test_no_navigation_reactions():
  # emotions come from the car and the driver only: no speed cameras, turns, speed bumps or arrival
  m, _ = ready()
  nav = {'camera', 'camera_fast', 'arrive', 'turn', 'bump'}
  assert not nav & set(m.PRIO) and not nav & set(m.COOLDOWN)
  assert not any(hasattr(FaceInputs(), k) for k in ('cam_dist', 'cam_limit', 'route_left', 'turn_type', 'turn_dist', 'bump_dist'))


def test_props_it_wears_while_things_last():
  m, inp = ready(engaged=True)
  inp.music_on = True                                     # the phone plays: headphones on
  run(m, inp, 1.0)
  assert m.P['p_phones'].v > 0.9
  inp.music_on = False
  run(m, inp, 1.0)
  assert m.P['p_phones'].v < 0.1
  inp.hazard = inp.left_blinker = inp.right_blinker = True   # hazard lights: the beacon on its head, as long as they are on
  run(m, inp, 4.0)
  assert m.P['p_beacon'].v > 0.9
  inp.hazard = inp.left_blinker = inp.right_blinker = False
  run(m, inp, 1.0)
  assert m.P['p_beacon'].v < 0.1
  inp.dm_alert = True                                     # a driver-monitoring alert: the whistle
  run(m, inp, 1.0)
  assert m.P['p_whistle'].v > 0.9
  inp.dm_alert = False
  run(m, inp, 1.0)
  assert m.P['p_whistle'].v < 0.1


def test_seatbelt_holds_up_the_buckle_then_puts_it_away():
  m, inp = ready(engaged=True)
  inp.seatbelt_off = True
  run(m, inp, 1.0)
  assert m.last_event == 'seatbelt' and m.P['p_buckle'].v > 0.9
  run(m, inp, 3.0)
  assert m.P['p_buckle'].v < 0.1
