import io
import struct

import numpy as np
import pytest

from openpilot.selfdrive.modeld.helpers import dump_oob, load_oob


def test_warp_buffer_roundtrip_does_not_change_native_pickle_format():
  value = {'identity': (1344, 760, 123456, 'model'), 'arrays': [np.arange(20, dtype=np.uint8), np.eye(3, dtype=np.float32)]}
  stream = io.BytesIO()
  dump_oob(value, stream)
  stream.seek(0)
  result = load_oob(stream)
  assert result['identity'] == value['identity']
  for old, new in zip(value['arrays'], result['arrays'], strict=True):
    np.testing.assert_array_equal(old, new)
    assert old.dtype == new.dtype


@pytest.mark.parametrize('payload', [b'', b'x', struct.pack('<q', -1), struct.pack('<q', 100) + b'x', struct.pack('<q', 65 << 20)])
def test_truncated_or_oversized_warp_pickle_is_rejected(payload):
  with pytest.raises((EOFError, ValueError)):
    load_oob(io.BytesIO(payload))
