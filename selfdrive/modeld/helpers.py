import json
import io
import os
import pickle
import shutil
import struct
import tempfile
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent / 'models'
TG_INPUT_DEVICES_PATH = MODELS_DIR / 'tg_input_devices.json'
USBGPU_VID = 0xADD1
USBGPU_PID = 0x0001
USBGPU_ENABLE_ENV_VARS = ("USE_USBGPU", "ENABLE_USBGPU")


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

def usbgpu_present() -> bool:
  for d in Path("/sys/bus/usb/devices").glob("*"):
    try:
      if int((d / "idVendor").read_text(), 16) == USBGPU_VID and \
          int((d / "idProduct").read_text(), 16) == USBGPU_PID:
        return True
    except Exception:
      pass
  return False
