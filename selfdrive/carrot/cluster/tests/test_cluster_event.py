"""The cluster HUD's event line: the comma's current alert, read from selfdriveState, at the top centre of the ambient
screen (cluster_layout.cluster_event_from_alert / AmbientEventFade, carried by RouteLogParser into ClusterUiState)."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

BUNDLE = Path(__file__).resolve().parents[1]     # the cluster modules import each other by bare name, as they run
if str(BUNDLE) not in sys.path:
  sys.path.insert(0, str(BUNDLE))

from cluster_layout import AmbientEventFade, cluster_event_from_alert


def test_event_lines_and_level_from_the_alert():
  assert cluster_event_from_alert("전방 주의", "전방 차량과 접근중", "userPrompt", "mid") == ("전방 주의", "전방 차량과 접근중", "warning")
  assert cluster_event_from_alert("브레이크!", "추돌 위험", "critical", "full") == ("브레이크!", "추돌 위험", "critical")
  assert cluster_event_from_alert("도로를 주시하세요", "", "normal", "small") == ("도로를 주시하세요", "", "info")
  assert cluster_event_from_alert("", "  only   the second\nline ", "normal", "small") == ("only the second line", "", "info")
  assert cluster_event_from_alert("a  b", " c\td ", "normal", "mid") == ("a b", "c d", "info")
  assert cluster_event_from_alert("hidden", "x", "normal", "none") == ("", "", "")     # no alert up
  assert cluster_event_from_alert("", "", "userPrompt", "mid") == ("", "", "")
  assert cluster_event_from_alert(None, None, None, None) == ("", "", "")


def run(fade, text, level, seconds, dt=0.05, detail=""):
  for _ in range(int(round(seconds / dt))):
    fade.update(text, level, dt, detail)
  return fade


def test_an_alert_fades_in_holds_and_fades_out():
  fade = AmbientEventFade(fade_in_s=0.2, fade_out_s=0.4, hold_s=0.5)
  run(fade, "", "", 1.0)
  assert fade.opacity == 0.0 and fade.text == ""
  run(fade, "전방 주의", "warning", 0.1, detail="전방 차량과 접근중")
  assert 0.4 < fade.opacity < 0.6 and (fade.text, fade.detail, fade.level) == ("전방 주의", "전방 차량과 접근중", "warning")
  run(fade, "전방 주의", "warning", 0.2)
  assert fade.opacity == 1.0
  run(fade, "", "", 0.45)                                   # gone, but held a moment
  assert fade.opacity == 1.0 and fade.text == "전방 주의"
  run(fade, "", "", 0.25)
  assert 0.0 < fade.opacity < 1.0                           # fading out
  run(fade, "", "", 0.5)
  assert fade.opacity == 0.0 and (fade.text, fade.detail, fade.level) == ("", "", "")


def test_an_alert_dropping_out_for_a_frame_does_not_flicker():
  fade = run(AmbientEventFade(), "정지합니다", "info", 1.0)
  fade.update("", "", 0.05)
  fade.update("정지합니다", "info", 0.05)
  assert fade.opacity == 1.0 and fade.since > 1.0             # same alert, no new pop-in


def test_an_alert_at_another_level_replaces_the_text_at_once_and_pops_in_again():
  fade = run(AmbientEventFade(fade_in_s=0.2), "정지합니다", "info", 1.0)
  fade.update("브레이크!", "critical", 0.0, "추돌 위험")
  assert (fade.text, fade.detail, fade.level) == ("브레이크!", "추돌 위험", "critical")
  assert fade.opacity == pytest.approx(0.35) and fade.since == 0.0
  run(fade, "브레이크!", "critical", 0.2, detail="추돌 위험")
  assert fade.opacity == 1.0


def test_text_changing_within_one_alert_updates_in_place():
  fade = run(AmbientEventFade(), "캘리브레이션 진행중 : 4%", "info", 1.0, detail="24 km/h 이상의 속도로 주행하세요")
  fade.update("캘리브레이션 진행중 : 5%", "info", 0.05, "24 km/h 이상의 속도로 주행하세요")
  assert fade.text == "캘리브레이션 진행중 : 5%" and fade.opacity == 1.0 and fade.since > 1.0   # no dim, no slide


def test_a_new_alert_while_the_old_one_fades_out_pops_in():
  fade = run(AmbientEventFade(hold_s=0.5, fade_out_s=0.4), "전방 주의", "warning", 1.0)
  run(fade, "", "", 0.7)                                    # held 0.5 s, then fading
  assert 0.0 < fade.opacity < 1.0
  fade.update("조향 보조 꺼짐", "warning", 0.0, "최소 속도 10 km/h")
  assert fade.text == "조향 보조 꺼짐" and fade.since == 0.0


def test_bad_elapsed_time_does_not_break_it():
  fade = AmbientEventFade()
  fade.update("x", "info", float("nan"))
  fade.update("x", "info", -3.0)
  assert fade.opacity == 0.0
  fade.update("x", "info", 99.0)                            # a stall counts as at most half a second
  assert fade.opacity == 1.0


def test_the_parser_carries_the_alert_into_the_cluster_state():
  route = pytest.importorskip("cluster_route_replay")   # needs pycapnp, as on the device
  parser = route.RouteLogParser()
  parser._update_selfdrive_state(NS(personality="standard", alertText1="조향 보조 꺼짐", alertText2="최소 속도 10 km/h",
                                    alertStatus="userPrompt", alertSize="mid"))
  car_state = NS(vEgo=10.0, aEgo=0.0, steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=False, leftBlindspot=False,
                 rightBlindspot=False, gearShifter="drive", cruiseState=NS(enabled=True, speed=20.0, available=True))
  state = route.frame_to_state(parser._frame_from_car_state(car_state, 1.0))
  assert (state.event_text, state.event_detail, state.event_level) == ("조향 보조 꺼짐", "최소 속도 10 km/h", "warning")
  parser._update_selfdrive_state(NS(personality="standard", alertText1="", alertText2="", alertStatus="normal",
                                    alertSize="none"))
  state = route.frame_to_state(parser._frame_from_car_state(car_state, 1.1))
  assert (state.event_text, state.event_detail, state.event_level) == ("", "", "")
