# eGPU 큰 모델 (최신 당근에서 이식)

ajouatom carrot-wip(2026-11-17, `2b202df1`)의 USB eGPU 지원을 이 브랜치(6월 당근 구조)에 옮긴 것이다.
콤마 내장 GPU 모델은 그대로 두고, 조건을 갖춘 eGPU가 연결돼 있으면 고른 큰 모델을 eGPU에서 돌린다.

## 고를 수 있는 모델

| 모델 | 비고 |
|---|---|
| N16 Nav (기본) | 원본은 내비 입력(NOO)을 쓰지만, 이 브랜치에는 내비 입력 경로가 없어 지도·안내 입력이 0이다. 일반 주행 모델로 동작한다. |
| Flip Flop | 젯슨에서는 MD v3와 같은 ONNX를 쓰고, eGPU용은 별도 패키지 |
| Light speed | |
| Light speed 2 | |

MD v3는 원본에서 은퇴했다. 목록에는 안 나오고, 예전에 받아 둔 파일은 오프로드일 때 자동으로 지운다.

## 필요한 장비 (꽂기만 해서 되는 조건)

- GPU: AMD **RX 9060 / 9060 XT (gfx1200)** 만. RX 9070 계열(gfx1201)은 미리 컴파일된 패키지가 없어 안 된다.
- USB 브리지: tinygrad 전용 펌웨어가 들어간 ASM2464 브리지(USB ID `ADD1:0001` 또는 `3801:0001`, 제품 문자열 `custom`/`AS2462` 계열).
  일반 USB4/썬더볼트 eGPU 케이스(`174C:2464` 등 순정 펌웨어)는 그대로는 안 된다.
- 연결: 콤마가 USB 호스트여야 하고 5Gbps 이상(SuperSpeed)으로 잡혀야 한다. 같은 포트로 젯슨(Jetlink, 콤마가 장치 쪽)과 동시에 쓸 수는 없다.
- 전원: GPU에 별도 12V. 브리지가 USB로 보여도 GPU 전원·PCIe 링크가 준비됐다는 뜻은 아니다.
- 인터넷: 모델을 처음 쓸 때만 필요(모델 약 0.8GB, 런타임 약 2MB, GPU 펌웨어 약 2MB).

## 쓰는 법

1. 당근웹 → 설정 → 기기 → **eGPU 모델**에서 다음 부팅 모델을 고른다.
2. eGPU를 연결하고, 주차한 채 시동을 켜 GPU에 전원을 넣고, 인터넷을 연결한 뒤 콤마를 재부팅한다.
3. 부팅 화면에서 모델·전용 런타임·GPU 펌웨어를 받아 해시를 검증하고, eGPU에서 시험 계산을 한 번 한다.
4. 이후 시동부터는 인터넷 없이 eGPU로 주행 모델을 돌린다.

- **처음에는 연결한 채로 재부팅해야 한다.** 콤마가 켜진 뒤에 처음 꽂으면 그 부팅에는 받기를 시작하지 않는다. 연결 이력이 생긴 뒤에는 백그라운드에서도 받는다.
- 받기 실패, 영구 검증 실패, eGPU 미연결이면 그 부팅은 내장 모델로 달린다. 받기는 백그라운드에서 30초마다 다시 시도한다.
- 시험 계산이 GPU 전원 없음·일시적 USB 오류로 실패하면 모델을 막지 않고 미룬다. 같은 부팅에서 modeld가 eGPU를 다시 시도하고, 그래도 안 되면 내장 모델로 간다.
- 주행 중 eGPU가 끊기거나 오류가 나면 내장 모델로 넘어간다(새 카메라 프레임부터). 정상 로드 뒤의 USB·시간 초과 오류에 한해 10/30/60초 간격으로 최대 세 번 다시 붙이고, 정차하거나 핸들을 잡았을 때 eGPU로 돌아간다. 시작할 때부터 eGPU 로딩에 실패한 부팅은 재연결하지 않는다.
- 파일은 `/data/media/0/carrot/models`에 저장한다.

## AGNOS 18.4 와 GPU 펌웨어

원본은 AGNOS 19.8(당근 수정판), 이 브랜치는 AGNOS 18.4를 쓴다. 18.4의 `/lib/firmware/amdgpu/*.zst`는
압축 헤더에 원래 크기가 없어 런타임의 `zstandard.decompress()`가 실패하고, 일부(me·mec·pfp·smu)는 런타임이 기대하는
버전보다 오래됐다. 그대로 두면 eGPU 시작이 실패하고 모델이 차단된다. 그래서 설치할 때 런타임이 고정한 펌웨어 8개를
검증해 `precompiled/<모델 해시>/amdgpu-firmware/`에 저장하고(`selfdrive/modeld/egpu_firmware.py`), 워커는 저장본을 쓴다.
펌웨어가 다 준비돼야 '설치됨'으로 본다.

## 검증한 것과 못 한 것

- 맥: 원본 eGPU 테스트(이 브랜치에 맞게 조정)와 펌웨어 테스트, 당근웹 API 테스트. 실제 서버에서 Light speed 2 설치 전체(받기·해시·런타임·펌웨어·설치 판정).
- AGNOS 19.8 콤마(읽기만): 펌웨어 8개 해시가 런타임 4종과 일치, comma 사용자가 root 그룹이라 `/dev/bus/usb` 쓰기 가능, libusb·LLVM 존재.
- 못 함: eGPU를 꽂은 실기기 동작(로드·추론·전환·재연결), AGNOS 18.4 기기의 USB 권한·커널 동작, 실제 주행.

## 바뀐 곳

- `selfdrive/modeld/`: `big_model*.py`, `precompiled_*.py`, `egpu_*.py`, `generic_model_runtime.py`, `local_gpu_warp.py`,
  `internal_model_warmup.py`, `output_validation.py`, `model_library.py`, `model_join.py`, `model_catalog/`,
  `egpu_prepare.py`·`egpu_firmware.py`(새로 작성)
- `selfdrive/modeld/modeld.py`: eGPU 시작, 실패 시 내장 모델 전환, 백그라운드 재연결
- `selfdrive/modeld/helpers.py`: eGPU 감지, 설치 경로
- `common/big_model_catalog.py`, `common/egpu_recovery_status.py`, `common/params_keys.h`(`UsbGpu*` 4개 추가)
- `selfdrive/selfdrived/selfdrived.py`: 첫 eGPU 로딩 중에는 레이더·통신 오류 대신 "System Initializing"으로 인게이지만 막음
- `launch_chffrplus.sh`: 부팅마다 모델 선택 고정, eGPU가 있으면 설치·검증, 백그라운드 받기
- 당근웹: `/api/egpu/model*` API와 기기 설정의 eGPU 모델 패널(`js/pages/egpu.js`)
