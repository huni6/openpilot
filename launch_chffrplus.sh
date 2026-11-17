#!/usr/bin/env bash

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null && pwd )"

source "$DIR/launch_env.sh"

function agnos_init {
  # TODO: move this to agnos
  sudo rm -f /data/etc/NetworkManager/system-connections/*.nmmeta
  rm -f /data/scons_cache/config.lock

  # set success flag for current boot slot
  sudo abctl --set_success

  # TODO: do this without udev in AGNOS
  # udev does this, but sometimes we startup faster
  sudo chgrp gpu /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0
  sudo chmod 660 /dev/adsprpc-smd /dev/ion /dev/kgsl-3d0

  # Check if AGNOS update is required
  if [ "$(< /VERSION)" != "$AGNOS_VERSION" ]; then
    AGNOS_PY="$DIR/system/hardware/tici/agnos.py"
    MANIFEST="$DIR/system/hardware/tici/agnos.json"
    # AGNOS 19.8-carrot ships separate images for comma 3 (tici) and comma 3X/4.
    MODEL="$(tr -d '\000\r\n' 2>/dev/null < /sys/firmware/devicetree/base/model | tr '[:upper:]' '[:lower:]')"
    MODEL="${MODEL#comma }"
    if [ "$MODEL" = "c3" ] || [ "$MODEL" = "tici" ]; then
      MANIFEST="$DIR/system/hardware/tici/agnos-tici.json"
    fi
    echo "AGNOS ${AGNOS_VERSION} update: model=${MODEL} manifest=${MANIFEST}"
    if python3 $AGNOS_PY --verify $MANIFEST; then
      sudo reboot
    fi
    $DIR/system/hardware/tici/updater $AGNOS_PY $MANIFEST
  fi
}

# AGNOS 19 (ported from carrot-wip) no longer ships several Python packages this
# branch imports, nor the legacy libjpeg/Eigen wrappers SConstruct needs. Install
# them offline from third_party/wheels into pydeps; never into the system venv.
function install_runtime_python_package {
  if python3 -m pip --version > /dev/null 2>&1; then
    python3 -m pip install --disable-pip-version-check --no-input --timeout 15 --retries 2 \
      --target "$PYDEPS" --upgrade "$@"
  elif command -v uv > /dev/null 2>&1; then
    UV_HTTP_TIMEOUT=15 UV_HTTP_RETRIES=2 uv pip install --python "$(command -v python3)" --no-python-downloads \
      --target "$PYDEPS" --upgrade "$@"
  else
    echo "No Python package installer is available."
    return 1
  fi
}

function ensure_python_package {
  local import_name="$1"
  local package_name="$2"
  local required="${3:-0}"
  local install_dependencies="${4:-0}"
  local wheel_dir="$DIR/third_party/wheels"

  if python3 -c "import ${import_name}" > /dev/null 2>&1; then
    return 0
  fi

  echo "${package_name} installing from local wheel."
  if [ "$install_dependencies" = "1" ]; then
    install_runtime_python_package --no-index --find-links "$wheel_dir" "$package_name"
  else
    install_runtime_python_package --no-index --no-deps --find-links "$wheel_dir" "$package_name"
  fi
  if [ "$?" = "0" ] && python3 -c "import ${import_name}" > /dev/null 2>&1; then
    echo "${package_name} installed."
    return 0
  fi

  python3 -c "import ${import_name}" >&2
  if [ "$required" = "1" ]; then
    echo "Required Python package ${package_name} is unavailable; not starting openpilot."
    return 1
  fi
  echo "Optional Python package ${package_name} is unavailable; continuing without it."
  return 0
}

function bootstrap_runtime_dependencies {
  # Only what this branch imports at runtime/build time and AGNOS 19 lacks.
  if ! ensure_python_package serial pyserial 1 || \
     ! ensure_python_package msgpack msgpack 1 || \
     ! ensure_python_package aiohttp aiohttp 1 1 || \
     ! ensure_python_package "aiortc; import av; import pylibsrtp" "aiortc==1.14.0" 1 1 || \
     ! ensure_python_package psutil psutil 1 || \
     ! ensure_python_package crcmod crcmod-plus 1 || \
     ! ensure_python_package jsonrpc json-rpc 1 || \
     ! ensure_python_package qrcode qrcode 1; then
    return 1
  fi

  ensure_python_package brotli brotli 0
  ensure_python_package usb pyusb 0
  ensure_python_package shapely shapely 0

  # SConstruct imports these native dependency wrappers; AGNOS 19 dropped them.
  if { [ -f /TICI ] || [ -f /AGNOS ]; } && \
     { ! ensure_python_package eigen eigen 1 || ! ensure_python_package libjpeg libjpeg 1; }; then
    return 1
  fi
}

function show_startup_failure {
  local message="$1"
  echo "$message"
  python3 "$DIR/system/ui/text.py" "$message" || true
  while true; do sleep 1; done
}

function start_carrot_recovery {
  local recovery_script="$DIR/selfdrive/carrot/recovery/server.py"
  local py_bin

  [ -f "$recovery_script" ] || return
  py_bin="$(command -v python3 || command -v python || true)"
  [ -n "$py_bin" ] || return

  if command -v pgrep >/dev/null 2>&1 && pgrep -f "selfdrive/carrot/recovery/server.py" >/dev/null 2>&1; then
    return
  fi

  echo "Starting carrot recovery server on 6999."
  (cd "$DIR" && "$py_bin" "$recovery_script" --port 6999 >> /tmp/carrot_recovery.log 2>&1 &)
}

function invalidate_modeld_build_if_needed {
  local stamp_path="$DIR/selfdrive/modeld/models/.build_stamp"
  local tg_devices_path="$DIR/selfdrive/modeld/models/tg_input_devices.json"
  local driving_pkl_path="$DIR/selfdrive/modeld/models/driving_tinygrad.pkl"
  local old_stamp

  MODEL_BUILD_STAMP_VALUE="$(git rev-parse HEAD:selfdrive/modeld HEAD:tinygrad_repo HEAD:common/file_chunker.py 2>/dev/null | tr '\n' ':')"
  if [ -z "$MODEL_BUILD_STAMP_VALUE" ]; then
    MODEL_BUILD_STAMP_VALUE="$(git rev-parse HEAD 2>/dev/null || true)"
  fi

  old_stamp="$(cat "$stamp_path" 2>/dev/null || true)"
  if [ "$MODEL_BUILD_STAMP_VALUE" != "$old_stamp" ] || [ ! -f "$tg_devices_path" ] || { [ ! -f "$driving_pkl_path" ] && [ ! -f "$driving_pkl_path.chunkmanifest" ]; }; then
    echo "Model/tinygrad inputs changed, invalidating generated modeld artifacts."
    rm -f "$DIR"/selfdrive/modeld/models/*_tinygrad.pkl*
    rm -f "$DIR"/selfdrive/modeld/models/*_metadata.pkl
    rm -f "$DIR"/selfdrive/modeld/models/tg_input_devices.json
    FORCE_REBUILD=1
  fi
}

function invalidate_native_build_if_needed {
  local missing=0
  local path

  for path in "$DIR/system/loggerd/loggerd" "$DIR/system/loggerd/encoderd" "$DIR/system/camerad/camerad"; do
    if [ ! -x "$path" ]; then
      echo "Missing native binary: $path"
      missing=1
    fi
  done

  if [ "$missing" = "1" ]; then
    FORCE_REBUILD=1
  fi
}

function prepare_big_model_if_needed {
  # Carrot eGPU (ported from carrot-wip): with an attached eGPU, install and
  # smoke-test this boot's selected precompiled model before starting modeld.
  # Any failure keeps the internal model for this boot; driving is never blocked
  # on the network. The background updater below keeps retrying the download.
  unset CARROT_BIG_MODEL_STARTUP_FAILED
  if ! python3 -c 'from openpilot.selfdrive.modeld.helpers import usbgpu_present; raise SystemExit(0 if usbgpu_present() else 1)' 2>/dev/null; then
    return
  fi

  if ! flock /tmp/big_model_update.lock python3 -m openpilot.selfdrive.modeld.big_model --prepare-for-startup; then
    echo "Selected eGPU model preparation failed; using the internal model this boot."
    export CARROT_BIG_MODEL_STARTUP_FAILED=1
    return
  fi
  if ! python3 -m openpilot.selfdrive.modeld.egpu_prepare; then
    echo "Selected eGPU model is not ready; using the internal model this boot."
  fi
}

function start_big_model_update {
  local log_path="/tmp/big_model_update.log"
  local lock_path="/tmp/big_model_update.lock"

  if command -v pgrep >/dev/null 2>&1 && pgrep -f '[o]penpilot.selfdrive.modeld.big_model --ensure-if-egpu' >/dev/null 2>&1; then
    return
  fi

  echo "Checking optional eGPU model in background (log: ${log_path})."
  (
    exec 9>"$lock_path"
    flock -n 9 || exit 0
    if command -v ionice >/dev/null 2>&1; then
      PYTHONUNBUFFERED=1 ionice -c 3 nice -n 10 python3 -m openpilot.selfdrive.modeld.big_model --ensure-if-egpu --network-wait-seconds 60 --retry-network
    else
      PYTHONUNBUFFERED=1 nice -n 10 python3 -m openpilot.selfdrive.modeld.big_model --ensure-if-egpu --network-wait-seconds 60 --retry-network
    fi
  ) >> "$log_path" 2>&1 &
}

function launch {
  # Remove orphaned git lock if it exists on boot
  [ -f "$DIR/.git/index.lock" ] && rm -f $DIR/.git/index.lock

  # Check to see if there's a valid overlay-based update available. Conditions
  # are as follows:
  #
  # 1. The DIR init file has to exist, with a newer modtime than anything in
  #    the DIR Git repo. This checks for local development work or the user
  #    switching branches/forks, which should not be overwritten.
  # 2. The FINALIZED consistent file has to exist, indicating there's an update
  #    that completed successfully and synced to disk.

  if [ -f "${DIR}/.overlay_init" ]; then
    find ${DIR}/.git -newer ${DIR}/.overlay_init | grep -q '.' 2> /dev/null
    if [ $? -eq 0 ]; then
      echo "${DIR} has been modified, skipping overlay update installation"
    else
      if [ -f "${STAGING_ROOT}/finalized/.overlay_consistent" ]; then
        if [ ! -d /data/safe_staging/old_openpilot ]; then
          echo "Valid overlay update found, installing"
          LAUNCHER_LOCATION="${BASH_SOURCE[0]}"

          mv $DIR /data/safe_staging/old_openpilot
          mv "${STAGING_ROOT}/finalized" $DIR
          cd $DIR

          echo "Restarting launch script ${LAUNCHER_LOCATION}"
          unset AGNOS_VERSION
          exec "${LAUNCHER_LOCATION}"
        else
          echo "openpilot backup found, not updating"
          # TODO: restore backup? This means the updater didn't start after swapping
        fi
      fi
    fi
  fi

  # handle pythonpath
  ln -sfn $(pwd) /data/pythonpath
  PYDEPS="$DIR/pydeps"
  mkdir -p "$PYDEPS"
  export PYTHONPATH="$PYDEPS:$PWD${PYTHONPATH:+:$PYTHONPATH}"

  start_carrot_recovery

  # hardware specific init
  if [ -f /AGNOS ]; then
    agnos_init
  fi

  # AGNOS must be current before installing its matching offline wheels, and
  # SCons imports native dependency modules, so do this before any build.
  # The bundled wheels target AGNOS/aarch64; PC environments manage their own.
  if { [ -f /AGNOS ] || [ -f /TICI ]; } && ! bootstrap_runtime_dependencies; then
    show_startup_failure "Runtime dependency installation failed. Check /data/openpilot/third_party/wheels."
  fi

  FORCE_REBUILD=0
  invalidate_modeld_build_if_needed
  invalidate_native_build_if_needed
  # Accept the pending external (eGPU) model choice once per real boot, before modeld.
  if ! flock /tmp/big_model_selection.lock python3 -m openpilot.common.big_model_catalog; then
    echo "External model selection unavailable; keeping the catalog default."
  fi
  prepare_big_model_if_needed

  rm selfdrive/pandad/*.so
  # write tmux scrollback to a file
  tmux capture-pane -pq -S-1500 > /tmp/launch_log
  if python -c "import flask" > /dev/null 2>&1; then
    echo "Flask already installed."
  else
    echo "Flask installing."
    pip install flask
  fi
  if python -c "import shapely" > /dev/null 2>&1; then
    echo "shapely already installed."
  else
    echo "shapely installing."
    pip install shapely
  fi
  if python -c "import kaitaistruct" > /dev/null 2>&1; then
    echo "kaitaistruct already installed."
  else
    echo "kaitaistruct installing."
    pip install kaitaistruct
  fi
  if python3 -c "import msgpack" > /dev/null 2>&1; then
    echo "msgpack already installed."
  else
    MSGPACK_WHEEL_DIR="$DIR/third_party/wheels"
    if ls "$MSGPACK_WHEEL_DIR"/msgpack-*.whl > /dev/null 2>&1; then
      echo "msgpack installing from local wheel to pydeps."
      python3 -m pip install --no-index --find-links "$MSGPACK_WHEEL_DIR" --target "$PYDEPS" --upgrade msgpack || python3 -m pip install --target "$PYDEPS" --upgrade msgpack
    else
      echo "msgpack local wheel missing, installing to pydeps from network."
      python3 -m pip install --target "$PYDEPS" --upgrade msgpack
    fi

    if python3 -c "import msgpack" > /dev/null 2>&1; then
      echo "msgpack installed for python3."
    else
      echo "msgpack install failed for python3."
    fi
  fi

  # events language init
  #LANG=$(cat ${PARAMS_ROOT}/d/LanguageSetting)
  LANG=$(cat /data/params/d/LanguageSetting)
  GITSTAT=$(git status)

  # events.py 한글로 변경 및 파일이 교체된 상태인지 확인
  if [ "${LANG}" = "ko" ] && [[ ! "${GITSTAT}" == *"modified:   selfdrive/selfdrived/events.py"* ]]; then
    cp -f $DIR/selfdrive/selfdrived/events.py $DIR/scripts/add/events_en.py
    cp -f $DIR/scripts/add/events_ko.py $DIR/selfdrive/selfdrived/events.py
  elif [ "${LANG}" = "zh-CHS" ] && [[ ! "${GITSTAT}" == *"modified:   selfdrive/selfdrived/events.py"* ]]; then
    # Backup current events.py (assumed English) and install Simplified Chinese events
    cp -f $DIR/selfdrive/selfdrived/events.py $DIR/scripts/add/events_en.py
    cp -f $DIR/scripts/add/events_zh.py $DIR/selfdrive/selfdrived/events.py
  elif [ "${LANG}" = "en" ] && [[ "${GITSTAT}" == *"modified:   selfdrive/selfdrived/events.py"* ]]; then
    cp -f $DIR/scripts/add/events_en.py $DIR/selfdrive/selfdrived/events.py
  fi

  # openpilot default ssh key installer
  if [ ! -f /data/params/d/GithubSshKeys ]; then
    echo -n openpilot > /data/params/d/GithubUsername
    cat /usr/comma/setup_keys > /data/params/d/GithubSshKeys
  fi

  # always ssh enable
  if [ "$(cat /data/params/d/SshEnabled 2>/dev/null)" = "0" ]; then
    echo -n 1 > /data/params/d/SshEnabled
  fi

  # start manager
  cd system/manager
  if [ "$FORCE_REBUILD" = "1" ] || [ ! -f $DIR/prebuilt ]; then
    if ! ./build.py; then
      echo "openpilot build failed, not starting manager."
      while true; do sleep 1; done
    fi
    if [ "$FORCE_REBUILD" = "1" ]; then
      mkdir -p "$DIR/selfdrive/modeld/models"
      echo -n "$MODEL_BUILD_STAMP_VALUE" > "$DIR/selfdrive/modeld/models/.build_stamp"
    fi
  fi
  start_big_model_update
  ./manager.py

  # if broken, keep on screen error
  while true; do sleep 1; done
}

launch
