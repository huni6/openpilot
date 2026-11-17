# AGNOS 19.8-carrot-bt1 로 올리기 (내부 당근 코드는 6월 버전 유지)

이 브랜치(6월 당근 구조)의 OS를 AGNOS 18.4에서 원본 당근과 같은 19.8-carrot-bt1로 올렸다. 주행·화면 코드는 그대로 두고,
19.8에서 빠진 것만 채웠다.

## 바뀐 것

- `launch_env.sh`: `AGNOS_VERSION="19.8-carrot-bt1"`.
- `system/hardware/tici/agnos.json`(comma 3X·4), `agnos-tici.json`(comma 3), `agnos.py`: 원본 그대로. 19.8 이미지는
  ajouatom/agnos-builder GitHub 릴리스에 있다(시스템 이미지 내려받기 약 1.0GB, 펼치면 4.7GB). 새 `agnos.py`는 다운로드
  캐시·이어받기·재시도·잠금이 들어간 원본 버전이다.
- `launch_chffrplus.sh`와 `system/updated/updated.py`(백그라운드 업데이트): 기종을 읽어 comma 3이면 `agnos-tici.json`을 쓴다.
  업데이트 화면은 예전 `updater`를 그대로 쓴다(`agnos.py --swap` 호출과 진행률 형식이 맞는다).
- 19.8 기본 파이썬 환경에 없는 패키지를 `third_party/wheels`(원본 휠 32개, 약 55MB)에서 오프라인으로 `pydeps`에 설치한다
  (`bootstrap_runtime_dependencies`, 빌드 전에 실행).
  - 필수: pyserial(GPS·모뎀), msgpack, aiohttp(당근웹), aiortc·av(영상 전송), psutil, crcmod(qcomgpsd), json-rpc(athena), qrcode,
    eigen·libjpeg(빌드).
  - 선택: brotli, pyusb, shapely.
  - 원본에 있던 OpenCV·acados·json11·catch2 휠은 넣지 않았다. 이 브랜치는 acados·json11·catch2를 저장소 안에 갖고 있고 OpenCV는 쓰지 않는다.
- `SConstruct`: 기기에서는 bzip2·libyuv 래퍼를 불러오지 않는다(기기 빌드 대상이 안 씀). 19.8의 FFmpeg는 공유 라이브러리라
  실행 파일에 그 경로(RPATH)를 넣는다.
- `system/loggerd/xattr_cache.py`: `xattr` 패키지 대신 표준 `os.getxattr`(원본과 같음).
- `restart.sh`: 이전 실행에서 남은 `AGNOS_VERSION`을 지우고 다시 시작한다.

## 19.8 콤마에서 직접 확인한 것 (읽기만)

- 기본 파이썬 환경에 없는 것: libjpeg·libyuv·bzip2 래퍼, aiohttp, aiortc, av, brotli, crcmod, json-rpc, pyserial, xattr 등.
- 있는 것: eigen, ffmpeg(공유 라이브러리), capnp 2.1.0, numpy 2.5.1, casadi 3.6.7, pyzmq, raylib 6.0.0.1(post98).
- 시스템 라이브러리 x264·va·va-drm·drm·z·jpeg 있음(loggerd 링크용).
- 6월 화면 코드가 쓰는 raylib 이름 183개가 19.8의 raylib 6.0에 모두 있다(raygui의 Gui*·gui_* 포함).
- pip 26.2·uv 있음, `/data` 여유 29GB.

## 처음 업데이트할 때

1. 18.4 기기에서 이 커밋을 받고 재부팅하면 업데이트 화면이 뜬다. 설치 버튼을 누르면 AGNOS 19.8을 내려받아(인터넷 필요,
   약 1GB) 다른 슬롯에 설치하고 다시 부팅한다. 백그라운드 업데이트가 미리 받아 둔 경우에는 버튼 없이 바로 전환한다.
2. 19.8로 올라온 뒤 휠을 `pydeps`에 설치하고, 전체 빌드를 다시 한다(시간이 걸린다).
3. 이미 19.8인 기기(원본 당근·WIP-FR 사용 기기)는 OS를 다시 굽지 않는다. 예전 이 브랜치(18.4)를 깔면 18.4로 내려가던 문제가 없어진다.

## 지원 범위

- comma 3X·4: 이번 변경으로 막히는 곳은 찾지 못했다(Codex 검토, 실기기 미검증).
- comma 3: 이 브랜치의 6월 코드에는 AR0231 카메라 드라이버가 없어 AR0231 센서 기기는 카메라를 쓸 수 없다. 또 외장 F4 판다(DOS 등)는
  판다 서명 확인이 H7 기준으로 되어 있어 연결 확인에서 막힌다. 둘 다 이번 변경 전부터 있던 문제이고 내부 코드를 유지하는 범위라 고치지 않았다.

## 위험과 되돌리기

- 실기기에서 아직 부팅해 보지 않았다. 18→19 사이 커널·드라이버 차이(카메라·센서·소리·판다·젯슨 USB)와 raylib 5.5→6.0의
  동작 차이는 코드만으로는 확인할 수 없다. 원본 이력이 10/03에 하나로 합쳐져 있어, 원본이 19로 넘어가며 같이 고친 다른
  부분을 커밋 단위로 찾을 수 없다.
- 빌드나 실행이 실패하면 화면에 오류가 남는다. 되돌리려면 18.4를 쓰는 바로 앞 커밋(`137d8a95`)으로 바꾸고
  재부팅하면 AGNOS 18.4를 다시 굽는다.
