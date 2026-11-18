# C4 로봇 얼굴 (WIP-FR에서 이식)

leehyuk1108/carrotpilot `WIP-FR`의 C4 로봇 얼굴(`NewUi` 3, 기준 커밋 `360d70dc`)을 이 브랜치에 옮긴 것이다.
comma 4(mici) 온로드 화면 전용이며 comma 3·3X 화면은 바뀌지 않는다.

## 켜는 법

당근웹 → 설정 → 화면 표시 → 정보 표시 → **new ui**(`NewUi`)에서 **3**을 고른다. 0은 기존 당근 UI다.
약 0.5초 안에 재부팅 없이 바뀐다.

값 번호는 WIP-FR과 같다. WIP-FR의 1(Wayon 스타일)과 2(시계)는 이 브랜치에 없어서 당근웹에는 0과 3만 나오고,
다른 경로로 1·2가 저장돼 있어도 기존 당근 UI로 보인다.

## 보이는 것

- 주행 중 카메라·차선·속도 패널·운전자 감시 아이콘·오른쪽 상태 영역을 끄고, 536×240 화면 전체에 빛나는 두 눈을 그린다.
- 차 상태에 따라 표정과 몸짓이 바뀐다.
  - 인게이지하면 핸들을 잡고 실제 조향 방향으로 돌린다.
  - 차선 변경, 사각지대, 앞차 출발, 끼어들기, 급제동·전방 충돌 경고, 운전자 감시 경고, 안전벨트, 후진·주차 기어, 비상등, 핸들 개입, 급커브에 반응한다.
  - 크루즈 설정속도가 바뀌면 눈 자리에 큰 숫자로 보여 준다.
  - 45초 넘게 정차하면 잠옷 모자를 쓰고 잔다.
  - 조향 토크가 셀수록 힘을 준다.
- 경고는 이렇게 처리한다.
  - 빨간 위급 경고만 얼굴 위에 뜬다.
  - 일반·안내 경고창은 띄우지 않고 로봇이 반응으로 대신한다. 경고음은 그대로 난다.
- eGPU 상태 배지는 평소 C4 화면과 같은 오른쪽 위에 그대로 뜬다(`docs/carrot_egpu.md`). 젯슨 상태도 오른쪽 아래 원래 자리에 뜬다.
  로봇이 머리 옆에 느낌표·물음표를 띄우는 잠깐 동안은 배지와 겹칠 수 있다.
- 화면 표시만 바꾸며 경고 판단·소리·주행 제어·운전자 감시 판단은 그대로다.

## WIP-FR과 다른 점

- 나브디가 없어 폰 음악 재생 상태를 받지 못한다. 그래서 헤드폰을 쓰고 춤추는 반응은 실내 마이크(`micd`의 `rawAudioData`)가
  박자를 잡을 때만 나온다. WIP-FR 실차에서 차량 스피커 음악은 마이크로 거의 잡히지 않았으므로 춤은 드물 것이다.
- 정차·오토홀드 타이머가 이 브랜치에는 없어 따로 숨길 것이 없다.
- 이 브랜치 당근웹은 선택지 이름 목록을 지원하지 않아, 카탈로그의 `values: [0, 3]`(허용값 목록)을 새로 읽게 했다(`js/pages/setting.js`).

## 검증한 것과 못 한 것

- 맥: WIP-FR 로봇 테스트 79개, 이 브랜치 연결 테스트(`selfdrive/ui/tests/test_robot_ui.py`) 13개, eGPU·젯슨 배지 테스트가 함께 통과.
  raylib 5.5로 로봇 장면 38개와 eGPU 배지를 겹친 장면을 그려 확인했다. 당근웹 설정은 0·3 두 버튼만 나오는 것을 Node로 확인했다.
- 못 함: 이 브랜치를 올린 comma 4 실기기 화면, 실주행 반응. 같은 로봇 코드가 AGNOS 19.8 콤마에서 WIP-FR로 돌고 있지만, 이 브랜치로는 확인하지 않았다.

## 바뀐 곳

- `selfdrive/ui/mici/onroad/robot_face.py`, `robot_face_model.py`, `robot_face_music.py`: WIP-FR 그대로(`cereal` 불러오기 경로만 이 브랜치에 맞춤)
- `selfdrive/ui/mici/onroad/augmented_road_view.py`: 온로드에서 `NewUi` 3이면 카메라 대신 로봇을 전체 폭으로 그림
- `selfdrive/ui/mici/onroad/alert_renderer.py`: 로봇 화면에서는 위급 경고만 표시
- `selfdrive/ui/mici/onroad/hud_renderer.py`: eGPU 배지 그리기를 따로 불러 쓸 수 있게 분리
- `selfdrive/ui/ui_state.py`: `NewUi`를 0.5초마다 읽음
- `common/params_keys.h`(`NewUi`), `selfdrive/carrot_settings.json`, 당근웹 `js/pages/setting.js`·`index.html`
- 테스트: `selfdrive/ui/mici/tests/test_robot_face*.py`, `preview_robot_face.py`(창 초기화만 이 브랜치에 맞춤), `selfdrive/ui/tests/test_robot_ui.py`
