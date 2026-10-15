"""A raw asset must not become a dirty LFS pointer merely by checking status."""
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_lfs_filter_only_matches_pointer_backed_assets():
  def git(*args, **kwargs):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], **kwargs)
  paths = git('ls-files', '-z')
  attributes = git('check-attr', '-z', '--stdin', 'filter', input=paths).split(b'\0')[:-1]
  filtered = [attributes[i].decode() for i in range(0, len(attributes), 3) if attributes[i + 2] == b'lfs']
  assert filtered
  for name in filtered:
    # Check size before reading: accidentally raw model blobs can be very large.
    assert int(git('cat-file', '-s', 'HEAD:' + name)) < 1024, name
    assert git('show', 'HEAD:' + name).startswith(b'version https://git-lfs.github.com/spec/v1\n'), name
