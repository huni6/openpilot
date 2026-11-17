"""Ported from carrot-wip tools/jetlink/test_model_library.py (Jetson host protocol cases omitted)."""
import sys
from types import SimpleNamespace

import pytest

from openpilot.common import big_model_catalog as C
from openpilot.selfdrive.modeld import model_library as library


@pytest.fixture
def store(monkeypatch, tmp_path):
  monkeypatch.setenv('CARROT_BIG_MODEL_DIR', str(tmp_path))
  monkeypatch.setattr(C, 'boot_id', lambda: 'boot-one')
  library._SIZES.clear()
  return tmp_path


def test_selection_waits_for_real_reboot_and_recovers_corrupt_settings(store, monkeypatch):
  assert C.freeze_boot() == 'n16'
  C.select('flip-flop')
  assert C.preferences()['selected'] == 'flip-flop'
  assert C.freeze_boot() == C.effective_id() == 'n16'
  monkeypatch.setattr(C, 'boot_id', lambda: 'boot-two')
  assert C.freeze_boot() == C.effective_id() == 'flip-flop'
  (store / 'selection.json').write_text('{invalid')
  assert C.preferences()['selected'] == 'n16'
  assert C.effective_id() == 'flip-flop'


@pytest.mark.parametrize('key', ['../n16', '', 'unknown', 'https://example/model'])
def test_only_reviewed_models_selectable(store, key):
  with pytest.raises(ValueError):
    C.select(key)
  assert not (store / 'selection.json').exists()




def test_delete_protects_boot_pending_and_active_but_removes_unused(store):
  C.freeze_boot()
  C.select('flip-flop')
  for key in ('n16', 'flip-flop'):
    with pytest.raises(ValueError):
      library._remove_cache(key)
  files = library.paths('md-v3')
  for path in files[:2]:
    path.write_bytes(b'model')
  files[2].mkdir(parents=True)
  (files[2] / 'model.pkl').write_bytes(b'copy')
  other = store / 'keep'
  other.write_text('keep')
  library._remove_cache('md-v3')
  assert not any(path.exists() for path in files)
  assert other.read_text() == 'keep'
  assert 'hidden' not in C.preferences()
  assert C.read_json(store / 'jetson-delete.json')['models'] == ['md-v3']
  assert 'md-v3' not in C.available()
  C.atomic_json(store / 'state.json', {'active': C.catalog()['md-v3']['egpu']})
  with pytest.raises(ValueError):
    library._remove_cache('md-v3')


def test_cache_inventory_counts_hardlink_once(store):
  import os

  first = store / 'first'
  first.write_bytes(b'123456')
  try:
    os.link(first, store / 'second')
  except OSError:
    pytest.skip('hardlinks unsupported')
  assert library.unique_bytes([store]) == 6






def test_jetson_inventory_is_not_presented_as_live_after_reboot(store):
  C.atomic_json(store / 'jetson-library.json', {'boot': 'old', 'updated': 100, 'models': {}})
  result = library.status()
  assert not result['jetson_inventory_fresh']
  assert all(row['jetson_bytes'] is None for row in result['models'])


@pytest.mark.parametrize('value', [{'selected': [], 'hidden': [{}]}, {'selected': None}, {'selected': 'gone'}])
def test_bad_preferences_never_select_unreviewed_model(store, value):
  C.atomic_json(store / 'selection.json', value)
  C.atomic_json(store / 'boot-selection.json', dict(value, boot=C.boot_id()))
  assert C.preferences()['selected'] == 'n16'
  assert C.freeze_boot() == C.effective_id() == 'n16'


def test_alias_delete_keeps_shared_selected_jetson_model(store):
  C.freeze_boot()
  C.select('flip-flop')
  library._remove_cache('md-v3')
  # Flip Flop shares the MD v3 ONNX on Jetson, so the selected shared identity stays.
  assert C.read_json(store / 'jetson-delete.json')['models'] == ['md-v3']
  assert C.model_spec('flip-flop')['sha256'] == C.model_spec('md-v3')['sha256']






def retire(monkeypatch, *keys):
  models = C.catalog()
  for key in keys:
    models[key]['retired'] = True
  monkeypatch.setattr(C, 'catalog', lambda: models)


def test_retirement_preserves_running_boot_and_falls_back_next_boot(store, monkeypatch):
  C.atomic_json(store / 'selection.json', {'selected': 'md-v3'})
  C.atomic_json(store / 'boot-selection.json', {'selected': 'md-v3', 'boot': C.boot_id()})
  retire(monkeypatch, 'md-v3')
  assert C.effective_id() == C.freeze_boot() == 'md-v3'
  assert C.preferences()['selected'] == 'n16'
  assert 'md-v3' not in C.available()
  with pytest.raises(ValueError):
    C.select('md-v3')
  offroad = SimpleNamespace(get_bool=lambda key: key == 'IsOffroad')
  assert library.reconcile_retired(offroad) == []
  monkeypatch.setattr(C, 'boot_id', lambda: 'next-boot')
  assert C.freeze_boot() == 'n16'
  assert library.reconcile_retired(offroad) == ['md-v3']


def test_curated_retirement_only_deletes_offroad_after_active_transition(store, monkeypatch):
  C.freeze_boot()
  retire(monkeypatch, 'md-v3')
  target = library.paths('md-v3')[0]
  target.write_bytes(b'old')
  keep = store / 'keep.txt'
  keep.write_text('keep')
  onroad = SimpleNamespace(get_bool=lambda key: key == 'IsOnroad')
  offroad = SimpleNamespace(get_bool=lambda key: key == 'IsOffroad')
  assert library.reconcile_retired(onroad) == [] and target.exists()
  C.atomic_json(store / 'state.json', {'active': C.catalog()['md-v3']['egpu']})
  assert library.reconcile_retired(offroad) == [] and target.exists()
  C.atomic_json(store / 'state.json', {'active': C.catalog()['n16']['egpu']})
  assert library.reconcile_retired(offroad) == ['md-v3']
  assert not target.exists() and keep.read_text() == 'keep'
  assert C.read_json(store / 'jetson-delete.json')['models'] == ['md-v3']
  assert 'md-v3' not in {m['id'] for m in library.status()['models']}


def test_hidden_preferences_migrate_to_full_curated_list(store):
  C.atomic_json(store / 'selection.json', {'selected': 'n16', 'hidden': ['md-v3', 'flip-flop']})
  assert {m['id'] for m in library.status()['models']} == set(C.available())




@pytest.mark.parametrize('model', ['flip-flop', 'light-speed', 'light-speed-2'])
def test_fresh_boot_selection_drives_the_egpu_manifest(store, model):
  import os
  import subprocess
  code = '''
from openpilot.common import big_model_catalog as C
C.select('flip-flop'); C.freeze_boot()
from openpilot.selfdrive.modeld.big_model import DEFAULT_MANIFEST_URL, fetch_manifest
assert DEFAULT_MANIFEST_URL == C.catalog()['flip-flop']['manifest_url']
assert fetch_manifest(DEFAULT_MANIFEST_URL).sha256 == C.catalog()['flip-flop']['egpu']['sha256']
C.select('n16')
assert C.effective_id() == 'flip-flop'
'''
  subprocess.run([sys.executable, '-P', '-c', code.replace('flip-flop', model)], env=dict(os.environ, PYTHONPATH=str(C.ROOT)), check=True)


def test_catalog_directory_resolves_in_this_source_layout():
  assert C.DIRECTORY.is_dir() and (C.DIRECTORY / 'catalog.json').is_file()
  assert set(C.available()) == {'n16', 'flip-flop', 'light-speed', 'light-speed-2'}
  for entry in C.catalog().values():
    assert entry['egpu']['filename'] == 'big_driving_tinygrad.pkl'
