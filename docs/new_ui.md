# C4 new ui 0~3 (WIP-FR에서 이식)

leehyuk1108/carrotpilot `WIP-FR`(기준 커밋 `5325a288`)의 comma 4(mici) 온로드 화면 4종을 이 브랜치에서 모두 쓸 수 있게 했다.
comma 3·3X 화면은 바뀌지 않는다. 화면 표시만 바꾸며 경고 판단·소리·주행 제어·운전자 감시 판단은 그대로다.

## 켜는 법

당근웹 → 설정 → 화면 표시 → **new ui**(`NewUi`)에서 고른다. 약 0.5초 안에 재부팅 없이 바뀐다.

| 값 | 화면 |
|---|---|
| 0 | 기존 당근 UI (오른쪽 상태 영역 있음) |
| 1 | Wayon/sunnypilot 스타일 |
| 2 | 아날로그 시계 |
| 3 | 로봇 얼굴 (`docs/robot_face.md`) |

0~3이 아닌 값(음수, 4 이상, 문자열, 참/거짓)은 0으로 본다. 1~3은 오프로드에서도 오른쪽 상태 영역 없이 전체 폭을 쓴다(WIP-FR과 같음).

## 1: Wayon 스타일

- 카메라와 차선은 그대로 그리되, 차선·도로경계를 모델 확신도만큼 옅은 흰 선으로 그린다(당근의 초록·노랑 강조 없음).
- 앞차는 당근의 사각 박스 대신 앞차 아래에 얇은 막대로 표시한다. 레이더 앞차는 초록, 비전 앞차는 밝은 파랑이며 약 0.2초에 걸쳐 나타나고 사라진다. 종제어를 오픈파일럿이 할 때만 그린다.
- 아래쪽에 조향 토크 막대, 왼쪽 아래에 핸들 아이콘(조향 중 초록, 위급 경고 때 빨간 핸들과 느낌표). 인게이지 없이 조향만 하는 중에도 토크 막대와 초록 핸들이 보인다.
- 크루즈 설정속도가 바뀌면 왼쪽 위에 큰 숫자와 MAX가 2.5초 뜬다.
- 사각지대에 차가 있으면 양옆에 Wayon BSM 아이콘이 뜨고, 그동안 일반 핸들 아이콘은 숨는다.
- 운전자 감시 아이콘은 왼쪽 위에 뜨되 경고나 MAX 표시가 있으면 비켜 준다.
- 당근 HUD의 속도·기어 패널, 오른쪽 신호등 원, 신뢰도 공은 그리지 않는다. TMAP 신호등 남은 시간은 위쪽 가운데(경고가 없을 때)에 그대로 뜬다.
- eGPU 배지는 오른쪽 위(경고가 없을 때), 젯슨 배지는 그 아래(eGPU 배지가 없으면 오른쪽 위)에 뜨며 경고가 뜨면 비켜 준다.

## 2: 시계

- 카메라·차선·HUD를 끄고 화면 전체에 아날로그 시계를 그린다. 왼쪽에 조향 상태(조향 중 초록 핸들)와 현재 속도, 오른쪽에 요일·날짜(한국 시간).
- 서울 기준 해가 지평선 아래 4도보다 내려가면 빨간 야간 모드로 바뀌고 초침을 숨긴다(해가 -3도 위로 올라오면 다시 낮). 콤마 시계가 맞춰지기 전에는 낮으로 둔다.
- 경고는 모두 시계 위에 그대로 뜬다. eGPU 배지는 오른쪽 위, 젯슨 배지는 오른쪽 아래 원래 자리에 뜬다.

## WIP-FR과 다른 점 (이 브랜치에 없는 것)

- 감속 사유 배지(CAM·TURN·ROUTE 등): `carrotMan.decelerationCandidates`가 이 브랜치 cereal에 없어서 뺐다.
- 정차·오토홀드 타이머: 이 브랜치에 없다. 시계·로봇 화면에서도 따로 숨길 것이 없다.
- VISION/BSD 상태 카드(비전 사각지대 감지): 이 브랜치에 없다.
- 리드 막대의 트래버스 레이더 표시 보정: 트래버스 전용이라 뺐다. 막대는 앞차 `yRel` 그대로의 위치에 그린다.
- 운전자 감시: WIP-FR의 휴대폰 아이콘·주황 시선 원뿔이 들어간 Wayon DM 미리보기 대신 이 브랜치의 기존 DM 아이콘을 쓴다.
- 시계의 해 높이 계산은 WIP-FR에서 나브디 앰비언트 모듈에 있던 것을 `clock_face.py` 안으로 옮겼다(나브디 없음).

## 검증한 것과 못 한 것

- 맥 테스트(아래 명령, `SCALE=1`은 화면이 꺼진 맥에서 raylib이 모니터 크기를 묻다 멈추지 않게 하려는 것):
  `selfdrive/ui/tests/test_robot_ui.py`(0~3 연결 27개), `selfdrive/ui/mici/tests/test_new_ui.py`(Wayon HUD 27개),
  `test_clock_face.py`(24개), `test_lead_bar.py`(79개)와 기존 로봇·eGPU·젯슨 배지 테스트가 함께 통과(합계 322개).
  ```bash
  SCALE=1 PYTHONPATH=$PWD:$PWD/.. <venv>/bin/python -m pytest -o addopts= -p no:cacheprovider --confcutdir=selfdrive/ui \
    selfdrive/ui/tests/test_robot_ui.py selfdrive/ui/tests/test_egpu_badge.py selfdrive/ui/tests/test_jetlink_badge.py \
    selfdrive/ui/mici/tests/test_robot_face.py selfdrive/ui/mici/tests/test_robot_face_model.py \
    selfdrive/ui/mici/tests/test_robot_face_music.py selfdrive/ui/mici/tests/test_clock_face.py \
    selfdrive/ui/mici/tests/test_lead_bar.py selfdrive/ui/mici/tests/test_new_ui.py
  ```
- raylib 5.5로 맥에서 시계 8장면(`preview_clock_face.py`), Wayon HUD 13장면(`preview_new_ui.py`, 카메라 대신 가짜 도로)을 그려 확인했다.
- 못 함: 이 브랜치를 올린 comma 4 실기기 화면·FPS, 실제 카메라 위 Wayon 화면, 실주행.

## 바뀐 곳

- `selfdrive/ui/ui_state.py`: `NewUi` 0~3, `new_ui`(1~3)·`clock_ui`(2)·`robot_ui`(3)
- `selfdrive/ui/mici/onroad/augmented_road_view.py`: 1~3 전체 폭, 2·3은 카메라 대신 시계·얼굴, 1의 DM 아이콘과 하단 페이드
- `selfdrive/ui/mici/onroad/hud_renderer.py`: 1이면 Wayon HUD, 1의 젯슨 배지 자리
- `selfdrive/ui/mici/onroad/model_renderer.py`: 1이면 흰 차선과 리드 막대(당근 앞차 박스 대신)
- `selfdrive/ui/mici/onroad/torque_bar.py`: 조향만 하는 중에도 막대를 보이는 옵션(`lateral_active`, 기본 꺼짐)
- 새 파일: `wayon_hud.py`, `blind_spot_indicators.py`, `lead_bar.py`, `clock_face.py`(모두 `selfdrive/ui/mici/onroad/`)
- 테스트·미리보기: `selfdrive/ui/tests/test_robot_ui.py`, `selfdrive/ui/mici/tests/test_new_ui.py`, `test_clock_face.py`,
  `test_lead_bar.py`, `preview_clock_face.py`, `preview_new_ui.py`

## 출처와 라이선스 (BSM 아이콘 표시)

`blind_spot_indicators.py`는 Wayon/sunnypilot의 `selfdrive/ui/sunnypilot/onroad/blind_spot_indicators.py`를 WIP-FR에서
옮겨 온 것이다. 원 저장소 라이선스 고지를 그대로 둔다.

This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license requiring permission for use.

This software is licensed under a custom license requiring permission for use.

### Custom MIT License

Copyright (c) 2024, Haibin Wen, SUNNYPILOT LLC

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to view and modify the Software, subject to the following conditions:

1. **Permission Required**: Permission Required for Commercial, For-Profit, or Closed Source Use: Use of the Software, in whole or in part, for any commercial purposes, for-profit projects, or in closed source projects requires explicit written permission from the original author(s).

2. **Redistribution**: Any redistribution of the Software, modified or unmodified, must retain this license notice and the following acknowledgment:
   "This software is licensed under a custom license requiring permission for use."

3. **Visibility**: Any project that uses the Software must visibly mention the following acknowledgment:
   "This project uses software from Haibin Wen and SUNNYPILOT LLC and is licensed under a custom license requiring permission for use."

4. **No Warranty**: THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Contact sunnypilot Support <support@sunnypilot.ai> for permission requests.
