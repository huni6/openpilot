import json
import io
import os
import pickle
import shutil
import struct
import tempfile
import time
from collections.abc import Collection
from pathlib import Path
from typing import TypeVar

from openpilot.common.file_chunker import get_manifest_path
from openpilot.selfdrive.modeld.big_model import selected_manifest

MODELS_DIR = Path(__file__).resolve().parent / 'models'
TG_INPUT_DEVICES_PATH = MODELS_DIR / 'tg_input_devices.json'
USBGPU_VID = 0xADD1
USBGPU_PID = 0x0001
USBGPU_ENABLE_ENV_VARS = ("USE_USBGPU", "ENABLE_USBGPU")
# Carrot precompiled eGPU support (ported from ajouatom carrot-wip).
USBGPU_USB_IDS = ((0xADD1, 0x0001), (0x3801, 0x0001))
USBGPU_MIN_SPEED_MBPS = 5000
USBGPU_TRANSIENT_INIT_TEXT = (
  "pcie link not up",
  "pcie power off failed",
  "pcie power on failed",
  "usb bridge reset failed",
  "read(0xb450",
  "f0 out failed: -1",
  "libusb_open: no such device",
  "amd:0 does not exist",
  "bulk out 0x02 failed: input/output error",
)
VisionStreamT = TypeVar("VisionStreamT")


def dump_oob(obj, stream):
  """Serialize the optional Jetlink warp without changing native model files."""
  with tempfile.TemporaryFile() as buffers:
    def capture(buffer):
      raw = buffer.raw()
      buffers.write(struct.pack('<q', raw.nbytes))
      buffers.write(raw)
      buffer.release()
    opcodes = io.BytesIO()
    pickle.Pickler(opcodes, protocol=5, buffer_callback=capture).dump(obj)
    data = opcodes.getvalue()
    stream.write(struct.pack('<q', len(data)))
    stream.write(data)
    buffers.seek(0)
    shutil.copyfileobj(buffers, stream)


def load_oob(stream):
  def read(size):
    data = stream.read(size)
    if len(data) != size:
      raise EOFError('Incomplete Jetlink warp buffer')
    return data
  length, = struct.unpack('<q', read(8))
  if not 0 < length <= 64 << 20:
    raise ValueError('Invalid Jetlink warp pickle size')
  opcodes = read(length)
  def buffers():
    while header := stream.read(8):
      size, = struct.unpack('<q', header)
      if not 0 <= size <= 256 << 20:
        raise ValueError('Invalid Jetlink warp buffer size')
      yield memoryview(bytearray(read(size)))
  return pickle.loads(opcodes, buffers=buffers())


def _default_tg_input_devices(process_name: str, usbgpu: bool):
  backend = os.getenv("DEV", "QCOM")
  defaults = {
    'selfdrive.modeld.modeld': {
      'default': {'WARP_DEV': backend, 'QUEUE_DEV': backend},
      'usbgpu': {'WARP_DEV': backend, 'QUEUE_DEV': 'AMD'},
    },
    'selfdrive.modeld.dmonitoringmodeld': {
      'default': {'DEV': backend},
    },
  }
  return defaults[process_name]['default' if not usbgpu else 'usbgpu']


def get_tg_input_devices(process_name: str, usbgpu: bool):
  try:
    with open(TG_INPUT_DEVICES_PATH) as f:
      return json.load(f)[process_name]['default' if not usbgpu else 'usbgpu']
  except FileNotFoundError:
    return _default_tg_input_devices(process_name, usbgpu)

def modeld_pkl_path(usbgpu: bool):
  prefix = 'big_' if usbgpu else ''
  return MODELS_DIR / f'{prefix}driving_tinygrad.pkl'

def usbgpu_enabled() -> bool:
  return any(os.getenv(name) == "1" for name in USBGPU_ENABLE_ENV_VARS)

def load_model_oob(f):
  """Protocol-5 loader for large precompiled eGPU artifacts (no Jetlink size limits)."""
  opcodes = f.read(struct.unpack('<q', f.read(8))[0])
  def buffers():
    prev = None
    while (h := f.read(8)):
      if prev is not None:
        prev.release()
      buf = bytearray(struct.unpack('<q', h)[0])
      if f.readinto(buf) != len(buf):
        raise EOFError("incomplete model buffer")
      prev = pickle.PickleBuffer(buf)
      yield prev
  return pickle.load(io.BytesIO(opcodes), buffers=buffers())


def select_vision_streams(available_streams: Collection[VisionStreamT], road_stream: VisionStreamT,
                          wide_stream: VisionStreamT, use_wide_camera: bool) -> tuple[VisionStreamT | None, bool]:
  """Select modeld camera inputs without waiting on a disabled wide camera."""
  if road_stream in available_streams:
    return road_stream, use_wide_camera and wide_stream in available_streams
  if use_wide_camera and wide_stream in available_streams:
    return wide_stream, False
  return None, False


def usb_device_present(usb_ids: Collection[tuple[int, int]], min_speed_mbps: int = 0) -> bool:
  for d in Path("/sys/bus/usb/devices").glob("*"):
    try:
      usb_id = (int((d / "idVendor").read_text(), 16), int((d / "idProduct").read_text(), 16))
      speed_mbps = float((d / "speed").read_text()) if min_speed_mbps > 0 else 0
      if usb_id in usb_ids and speed_mbps >= min_speed_mbps:
        return True
    except Exception:
      pass
  return False


def usbgpu_present() -> bool:
  # The custom bridge can remain enumerated over USB 2.0 while its SuperSpeed
  # link is unavailable. That state cannot support PCIe model loading.
  return usb_device_present(USBGPU_USB_IDS, USBGPU_MIN_SPEED_MBPS)


def wait_for_usbgpu_present(timeout: float, poll_interval: float = 0.1) -> bool:
  """Wait briefly for a remembered eGPU bridge to enumerate at SuperSpeed."""
  if usbgpu_present():
    return True

  deadline = time.monotonic() + max(0.0, timeout)
  poll_interval = max(0.01, poll_interval)
  while (remaining := deadline - time.monotonic()) > 0.0:
    time.sleep(min(poll_interval, remaining))
    if usbgpu_present():
      return True
  return False


def refresh_usbgpu_device_cache() -> None:
  """Discard libusb device pointers that may go stale during USB re-enumeration."""
  try:
    from tinygrad.runtime.support.usb import USB3
    USB3.list_devices.__func__.cache_clear()
  except (AttributeError, ImportError):
    pass


def usbgpu_pcie_not_ready(error: BaseException | str) -> bool:
  """Recognize the transient state between USB enumeration and PCIe link-up."""
  if isinstance(error, str):
    message = error.lower()
    return any(marker in message for marker in USBGPU_TRANSIENT_INIT_TEXT)

  pending: list[BaseException] = [error]
  seen: set[int] = set()
  while pending:
    current = pending.pop()
    if id(current) in seen:
      continue
    seen.add(id(current))
    message = str(current).lower()
    if any(marker in message for marker in USBGPU_TRANSIENT_INIT_TEXT):
      return True

    # tinygrad tries KFD, native PCIe, and USB PCIe backends together. When all
    # fail it reports an ExceptionGroup, so the transient USB link error is one
    # of the child exceptions rather than the direct cause/context.
    children = getattr(current, "exceptions", ())
    pending.extend(child for child in children if isinstance(child, BaseException))
    if current.__cause__ is not None:
      pending.append(current.__cause__)
    if current.__context__ is not None:
      pending.append(current.__context__)
  return False


def active_usbgpu_compiled_path() -> Path | None:
  """Installed, verified precompiled artifact for the current boot's model selection."""
  model = selected_manifest()
  if model is None:
    return None
  from openpilot.selfdrive.modeld.precompiled_model import installed
  if (precompiled := installed(model)) is not None:
    return precompiled
  if model.precompiled_only:
    return None
  # Raw ONNX entries would need this branch's local SCons big-model compiler.
  path = big_model_pkl_path(model.sha256)
  return path if Path(get_manifest_path(path)).is_file() else None


def big_model_pkl_path(model_sha256: str) -> Path:
  return MODELS_DIR / f'big_driving_{model_sha256[:16]}_tinygrad.pkl'


def usbgpu_compile_pending() -> bool:
  model = selected_manifest()
  if model is None:
    return False
  from openpilot.selfdrive.modeld.precompiled_model import installed
  if installed(model) is not None:
    return False
  return not Path(get_manifest_path(big_model_pkl_path(model.sha256))).is_file()


def usbgpu_compiled_path() -> Path | None:
  return active_usbgpu_compiled_path()


def usbgpu_compiled() -> bool:
  return usbgpu_compiled_path() is not None
