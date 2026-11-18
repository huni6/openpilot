# 조향 추가 설정: ej 클램프·횡가속·저크, 레인모드 전환, 무게중심

2026-11-19 사용자 요청으로 넣은 네 가지다. 모두 당근웹 → 설정 → 주행 제어 → **차량 조향**에 있다.

| 당근웹 위치 | 설정 | 출처 |
|---|---|---|
| 최소거리 클램프 | `ClampEnable`, `ClampMinDistLane`, `ClampMinDistEdge`, `ClampSteerGain`, `ClampSteerAlert` | aabc88/openpilot `ej` (WIP-FR이 다듬은 버전) |
| 횡가속·저크 한도 | `LatAccelMaxLow`, `LatAccelMaxHigh`, `LatJerkMax` | aabc88/openpilot `ej` 1b670d9b |
| 레인모드 | `LanelessCurvature`(레인리스 전환 곡률), `LaneModeWithLead`(앞차 있으면 레인모드) | 새로 작성 |
| 차량 제원 | `VehicleCenterToFrontRatio`(무게중심 앞축 거리 비율) | leehyuk1108/carrotpilot WIP-FR |

설정은 모두 재부팅 없이 바뀐다. 기본값에서 이전과 달라지는 것은 두 가지다.

- **최소거리 클램프는 ej·WIP-FR처럼 켜진 채로 들어간다.** 그래서 레인리스에서 모델 계획이 선에 20cm 넘게 붙으면 보정과 띵 알림이 걸린다.
  Codex 비교로는 60km/h에서 계획이 왼쪽 선 95cm 안쪽일 때 곡률 0.00288(횡가속 0.8m/s²)이 더해졌다. 이전처럼 쓰려면 `ClampEnable`을 끈다.
- 횡가속 한도의 속도 구간이 ej식으로 바뀌었다(아래).

나머지 새 설정은 기본값이 꺼짐이거나 이전 값과 같다.

## 1. ej 조향 수정

### 최소거리 클램프

ej(be252819 18c0344b 399efba7 916a79f0 6a06a6ce 2c50e890)의 클램프다.
WIP-FR이 Codex 교차검토로 고친 버전(d3c44125 ca180ae3 2356f130 1939d1fb, `min_distance_clamp.py`)을 그대로 옮겼다.

- 차 중심이 보이는 차선에서 1.35m, 차선이 안 보이는 쪽은 확실한 도로경계에서 1.15m 안으로 들어가지 않게 한다.
- 레인모드에서는 따라가는 경로를 넘은 만큼만 밀어낸다(`lane_planner_2.apply_min_distance_clamp`).
- 레인리스에서는 1초 앞 계획이 경계를 20cm 넘게 넘으면 모델 곡률에 보정을 더한다(`ClampSteerCorrector`).
  - 보정 크기는 횡가속 1.5m/s², 곡률 0.02 이하다.
  - 0.5초쯤에 걸쳐 들어가고, 10cm 아래로 줄면 꺼진다.
  - 보정이 걸리면 "클램프 조향 보정 작동"과 띵 소리를 낸다. 끝난 뒤 5초 동안은 다시 울리지 않는다.
- 다음 경우에는 즉시 0이 된다.
  - 차선변경 중, 깜빡이를 켠 동안, 그리고 그 뒤 2초
  - 클램프를 끄거나 게인 0
  - 5km/h 이하(10km/h까지 서서히 켜짐)
  - 모델 메시지가 오래됐거나 깨졌을 때
- ej는 설정을 10ms마다 8번 읽었지만, 이 버전은 controlsd가 1초에 한 번, 플래너가 0.5초에 한 번 읽는다.

### 횡가속·저크 한도

- `LatAccelMaxLow`(기본 45 = 4.5m/s²)는 80km/h 이하에서 쓴다.
- `LatAccelMaxHigh`(기본 30 = 3.0m/s²)는 120km/h 이상에서 쓴다.
- 80~120km/h 사이는 두 값을 직선으로 잇는다(ej·최신 당근과 같음).
  - **이전 이 브랜치는 100km/h에서 4.5→3.0으로 한 번에 바뀌었다.** 기본값에서도 80~100km/h는 한도가 조금 낮아지고(100km/h에서 3.75), 100~120km/h는 조금 높아진다.
  - 보통 고속 커브(반경 300m 이상)는 이 한도에 한참 못 미쳐 체감 차이는 거의 없을 것이다.
- `LatJerkMax`(기본 50 = 5.0m/s³)는 요청 곡률이 바뀌는 속도의 상한이다.
- 세 값은 controlsd가 1초마다 읽는다. 횡가속 한도는 커브 중에 갑자기 깎이지 않도록 초당 0.5m/s²씩 바뀐다.
- 범위(20~60, 20~100)를 벗어난 값이나 깨진 값은 기본값으로 본다.
- ej의 ComfortBrake, long_mpc 비용, 현대 정지 진입 속도 변경은 종제어라 요청 범위(조향) 밖이어서 가져오지 않았다.

## 2. 곡률 기준 레인리스 전환 (`LanelessCurvature`)

레인모드로 달리다가 일정 곡률 이상인 커브에서는 레인리스(모델 조향)로 바꾼다.

- 값×0.0001이 곡률(1/m)이다. 0은 끄기(기본)다.
  - 반경으로는 10000÷값 m다. 예: 40은 반경 250m보다 급한 커브, 100은 반경 100m보다 급한 커브다.
- 판단에 쓰는 곡률: 모델의 지금 요청 곡률(`action.desiredCurvature`)과 1초 앞까지 계획 곡률(`orientationRate.z ÷ velocity.x`) 중 큰 값.
  계획 속도 1m/s 미만은 뺀다.
- 설정값 이상이면 바로 레인리스로 간다. 설정값의 80% 아래가 1초 이어지면 레인모드로 돌아간다. 그래서 S자 커브에서 왔다 갔다 하지 않는다.
- controlsd가 조향 곡률의 출처만 바꾼다. 기존 `UseLaneLineCurveSpeed`(커브속도)와 같은 자리다.
  - 둘 중 하나만 걸려도 레인리스다.
  - 화면의 레인모드 표시(`controlsState.activeLaneLine`)도 같이 바뀐다.
- 레인리스로 바뀐 동안에는 클램프의 레인리스 보정이 적용된다.

## 3. 앞차 있으면 레인모드 (`LaneModeWithLead`)

- 켜면 앞차(`radarState.leadOne`, 화면에 앞차 표시가 뜨는 레이더·비전 앞차)가 0.5초 이상 보이는 동안 레인모드를 쓴다.
- 앞차가 2초 동안 안 보이면 원래 조건으로 돌아간다. 레이더·비전 앞차가 한두 프레임 끊겨도 깜빡이지 않게 하려는 것이다.
- 기존 속도 조건(`UseLaneLineSpeed`, LFA 버튼 토글 포함)과 OR다.
  - 속도 조건만 맞아도, 앞차 조건만 맞아도 레인모드다.
  - LFA 버튼으로 레인모드를 꺼 둔 경우에도 앞차가 있으면 레인모드가 된다.
  - 앞차가 있을 때만 레인모드를 쓰려면 `UseLaneLineSpeed`를 0으로 두고 이 설정을 켠다.
- 바뀌지 않은 조건이 그대로 이긴다.
  - 차선이 1초 넘게 잘 보여야 실제로 레인모드가 걸린다.
  - 모델이 크게 감속을 계획하는 동안(`laneless_only`)은 레인리스다.
  - 2번의 레인리스 전환 곡률 이상인 커브에서도 레인리스다.

## 4. 무게중심 앞축 거리 비율 (`VehicleCenterToFrontRatio`)

WIP-FR의 실시간 차량 제원(WIP-FR `docs/live_vehicle_specs_20261020.md`)에서 무게중심 비율만 옮겼다. 차량 무게(`VehicleMass`)는 옮기지 않았다.

- 앞바퀴 축에서 무게중심까지 거리 ÷ 축간거리다(0.10~0.90). 앞축 하중 비율이나 무게중심 높이가 아니다.
- 당근웹 기본값은 설치된 차량의 비율이다(`CarParamsPersistent`).
  - EV6는 d3cd21d9 이후 0.50이다.
  - 차량을 아직 인식하지 못했으면 항목이 보이지 않는다.
- 저장 방식
  - 차량 비율과 같은 값을 고르면 0으로 저장해 "차량 값을 따름"이 된다. 나중에 코드의 차량 기본값이 바뀌어도 옛 값에 묶이지 않는다.
  - 다른 값은 그대로 저장한다.
  - 70처럼 범위를 벗어난 값은 잘라서 저장하지 않고 거부한다(HTTP 400).
- 반영 경로(WIP-FR과 같음)
  - paramsd가 0.5초마다 읽어 초당 0.02씩 옮긴다. 0.50→0.45는 2.5초 걸린다.
  - 앞뒤 타이어 강성을 새 축 하중에 맞게 다시 계산한다.
  - paramsd는 칼만 필터 상수를 바꾸고(학습한 조향비·강성은 유지), 적용값을 `liveParameters.vehicleDynamics`로 보낸다.
  - controlsd(VehicleModel)와 레인모드 플래너(MPC 차량 계수)는 이 값만 쓴다. 메시지가 없거나 이상하면 마지막 값을 유지한다.
  - 받는 쪽도 초당 0.03(paramsd 속도의 1.5배)까지만 따라간다(`DynamicsFollower`). 그래서 `liveParameters`가 잠깐 무효였다가
    돌아와도 그동안 쌓인 변화가 한 번에 들어가지 않는다(Codex F5). 정상 램프는 늦지 않게 그대로 따라간다.
- 실측 근거 없이 조향감을 맞추려고 바꾸지 말 것.

## 검증한 것과 못 한 것

- 맥 테스트
  - 제어: `selfdrive/controls/tests/test_min_distance_clamp.py`(WIP-FR 테스트), `test_lateral_limits_and_lane_mode.py`, `test_vehicle_dynamics_consumers.py` 82개
  - 무게중심: `common/tests/test_vehicle_dynamics.py` 45개
  - 당근 서버: `selfdrive/carrot/server/tests` 61개(새 `test_vehicle_center_ratio.py` 포함)
- 실제 controlsd·lateral planner를 맥에서 돌렸다. 네이티브 IPC·Params·MPC만 대신하고, 메시지는 실제 capnp, 차량은 opendbc의 EV6 CarParams다.
  - 레인리스에서 왼쪽 차선 95cm 붙은 계획: 보정 0.00128(순수추종 계산값과 같음)이 들고 띵 플래그가 선다. 클램프를 끄면 0이다.
  - 레인모드에서 곡률 0.002 → 0.006(설정 40): 레인리스로 가고, 0.002로 돌아온 뒤 0.5초엔 그대로 레인리스, 1.5초엔 레인모드다. 설정 0이면 안 바뀐다.
  - 60km/h, 모델 0.03 요청: `LatAccelMaxLow` 45 → 4.5m/s², 20 → 2.0m/s². 20→45로 올리면 1초 뒤 읽고 초당 0.5씩 오른다.
  - `LatJerkMax` 50/20: 0.1초 뒤 곡률이 이론값(0.00080/0.00032)과 같다.
  - 무게중심 스냅숏 0.45: VehicleModel 앞축 거리가 1.450→1.305m. 메시지가 무효면 유지한다. 플래너 계수도 바뀐다.
  - 플래너: 토글 끄면 앞차로 안 바뀐다. 켜면 앞차 0.4초엔 그대로, 0.6초에 레인모드, 앞차 사라지고 1.5초엔 유지, 2.1초에 해제다. 속도 조건은 그대로 동작한다.
  - 경로 클램프는 −0.95m 계획을 −0.40m 경계까지 밀고, 끄거나 깜빡이를 켜면 그대로 둔다.
- 당근웹: 맥에서 실제 서버·웹 파일로 띄워 브라우저로 새 항목을 확인했다(Params는 메모리 대체, EV6 CarParams 0.50).
  - 무게중심 +/−로 0.51·0.52가 저장되고, 0.50으로 돌리면 0(차량 값 따름)이 저장된다.
  - 70은 거부된다.
  - 토글·곡률·new ui 0~3 버튼도 저장값을 확인했다.
- Codex 교차검토(27595aa7, 같은 테스트 505개 통과 확인)에서 나온 지적과 처리
  - F1 클램프 기본 켜짐: ej·WIP-FR과 같게 두고 위에 적었다.
  - F2 도로경계·차선 배열이 깨진 모델 메시지에서 경로 클램프가 예외를 내 plannerd가 멈출 수 있음: 배열을 확인하고, 계산이 안 되면 경로를 그대로 둔다.
    WIP-FR의 같은 코드에도 있는 결함이다.
  - F3 무효 `radarState`의 앞차도 앞차로 셈: `valid`도 확인한다.
  - F4 전체 설정 초기화가 무게중심을 차량 비율 숫자로 저장함: 초기화·프로필·JSON/QR 복원·파일 복원이 모두 거치는 쓰기 함수에서 차량 비율(또는 백업의 0)을 0(차량 값 따름)으로 저장한다.
    재검토에서 나온 남은 경로(이미 숫자로 고정된 값, 파일 업로드 복원)와 미리보기에 0이 보이던 문제도 고쳤다. 미리보기·응답에는 화면에 보이는 값(차량 비율)을 쓴다.
  - F5 위 `DynamicsFollower`.
- 못 함
  - 이 브랜치를 올린 실기기 부팅과 주행(EV6), 실제 Params 네이티브 등록 확인
  - 레인리스 보정·곡률 전환·앞차 레인모드의 실주행 느낌
  - 무게중심 변경이 실차 조향에 주는 영향

## 바뀐 곳

- `selfdrive/controls/lib/min_distance_clamp.py`(WIP-FR 그대로), `lane_mode_switch.py`(새로 작성)
- `selfdrive/controls/lib/drive_helpers.py`: `lateral_accel_limits`, `lateral_jerk_limit`, `clip_curvature` 한도 인자와 80~120km/h 구간
- `selfdrive/controls/controlsd.py`: 설정 1초 읽기, 횡가속 한도 서서히 변경, 레인리스 클램프 보정, 곡률 레인리스 전환, `clampSteerActive`, VehicleModel 무게중심 갱신
- `selfdrive/controls/lib/lateral_planner.py`, `lane_planner_2.py`: 경로 클램프, 앞차 레인모드, 플래너 차량 계수 갱신
- `selfdrive/selfdrived/selfdrived.py`, `events.py`: 클램프 알림
- `cereal/log.capnp`: `OnroadEvent.clampSteerActive @123`, `ControlsState.clampSteerActive @68`, `LiveParametersData.vehicleDynamics @21`
- `common/vehicle_dynamics.py`(새로 작성, WIP-FR 기반), `selfdrive/locationd/paramsd.py`, `opendbc_repo/opendbc/car/vehicle_model.py`(`update_dynamics`)
- `common/params_keys.h`, `selfdrive/carrot_settings.json`(차량 조향 메뉴에 최소거리 클램프·횡가속·저크 한도·차량 제원 묶음 추가)
- 당근 서버 `services/params.py`·`services/settings.py`·`features/params.py`·`features/settings.py`, 웹 `js/pages/setting.js`(소수 단위 설정)
