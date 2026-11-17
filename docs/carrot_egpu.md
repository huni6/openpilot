# eGPU 큰 모델 (최신 당근에서 이식)

ajouatom carrot-wip(2026-11-17, `2b202df1`)의 USB eGPU 지원을 이 브랜치(6월 당근 구조)에 옮긴 것이다.
콤마 내장 GPU 모델은 그대로 두고, eGPU가 연결돼 있으면 고른 큰 모델을 eGPU에서 돌린다.

## 고를 수 있는 모델

| 모델 | 내비 입력(NOO) | 비고 |
|---|---|---|
| N16 Nav (기본) | 지원 | |
| Flip Flop | 없음 | |
| Light speed | 없음 | |
| Light speed 2 | 없음 | |

MD v3는 원본에서 은퇴했다. 목록에는 안 나오고, 예전에 받아 둔 파일은 오프로드일 때 자동으로 지운다.

## 쓰는 법

1. 당근웹 → 설정 → 기기 → **eGPU 모델**에서 다음 부팅 모델을 고른다.
2. 주차·시동 OFF 상태에서 인터넷을 연결하고 콤마를 재부팅한다.
3. eGPU가 연결돼 있으면 부팅 화면에서 모델(약 0.8GB)과 전용 런타임을 받아 검증하고, eGPU에서 시험 계산을 한 번 한다.
4. 그 다음 시동부터 eGPU로 주행 모델을 돌린다.

- 받기 실패, 검증 실패, eGPU 미연결이면 그 부팅은 내장 모델로 달린다. 백그라운드에서 받기를 계속 다시 시도한다.
- 주행 중 eGPU가 끊기거나 오류가 나면 바로 내장 모델로 넘어간다. 그 뒤 최대 세 번 다시 붙이고, 정차하거나 핸들을 잡았을 때 eGPU로 돌아간다.
- 파일은 `/data/media/0/carrot/models`에 저장한다.

## 요구 사항과 한계

- eGPU는 AMD RDNA4(gfx1200, RX 9060/9070 계열)만 지원한다. 미리 컴파일된 패키지가 gfx1200용뿐이다.
- USB 브리지 ID는 `ADD1:0001` 또는 `3801:0001`이고, 5Gbps 이상으로 연결돼야 한다.
- 모델과 런타임은 원본 당근의 모델 서버(`upload.shind0.synology.me`)에서 받는다. SHA-256과 크기를 모두 확인한다.
- 이 브랜치에는 ONNX를 기기에서 직접 컴파일하는 경로가 없다. 카탈로그 모델은 모두 미리 컴파일된 패키지라서 필요 없다.
- 원본은 AGNOS 19.8 기준이고 이 브랜치는 AGNOS 18.4 기준이다. 실기기 eGPU 동작은 아직 검증하지 않았다.

## 바뀐 곳

- `selfdrive/modeld/`: `big_model*.py`, `precompiled_*.py`, `egpu_*.py`, `generic_model_runtime.py`, `local_gpu_warp.py`,
  `internal_model_warmup.py`, `output_validation.py`, `model_library.py`, `model_join.py`, `model_catalog/`, `egpu_prepare.py`(새로 작성)
- `selfdrive/modeld/modeld.py`: eGPU 시작, 실패 시 내장 모델 전환, 백그라운드 재연결
- `selfdrive/modeld/helpers.py`: eGPU 감지, 설치 경로
- `common/big_model_catalog.py`, `common/egpu_recovery_status.py`, `common/params_keys.h`(`UsbGpu*` 4개 추가)
- `selfdrive/selfdrived/selfdrived.py`: 첫 eGPU 로딩 중에는 레이더·통신 오류 대신 "System Initializing"으로 인게이지만 막음
- `launch_chffrplus.sh`: 부팅마다 모델 선택 고정, eGPU가 있으면 설치·검증, 백그라운드 받기
- 당근웹: `/api/egpu/model*` API와 기기 설정의 eGPU 모델 패널(`js/pages/egpu.js`)
