import json

import pytest

from sgz_agent.config import load_config


@pytest.mark.parametrize('data_dir', [None, '../../.state', '../custom-state'])
def test_backend_config_keeps_shared_state_default_and_honors_overrides(tmp_path, data_dir):
    config_path = tmp_path / 'backend/config/game.json'
    config_path.parent.mkdir(parents=True)
    settings = {'executable': str(tmp_path / 'game.exe')}
    if data_dir is not None:
        settings['data_dir'] = data_dir
    config_path.write_text(json.dumps(settings), encoding='utf-8')

    config = load_config(config_path)

    expected = (tmp_path / 'backend/custom-state' if data_dir == '../custom-state'
                else tmp_path / '.state')
    assert config.data_dir == expected
    assert not config.data_dir.exists()


def test_nested_client_config_defaults_to_original_state(tmp_path):
    path = tmp_path / 'project with spaces/backend/config/client/game.local.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'executable': str(tmp_path / 'game.exe')}))
    assert load_config(path).data_dir == tmp_path / 'project with spaces/.state'


def test_client_default_config_prefers_existing_legacy_path(tmp_path):
    from sgz_agent.client.config import default_config_path
    old = tmp_path / 'backend/config/game.local.json'
    new = tmp_path / 'backend/config/client/game.local.json'
    new.parent.mkdir(parents=True)
    new.write_text('{}')
    assert default_config_path(tmp_path) == new
    old.write_text('{}')
    assert default_config_path(tmp_path) == old


def test_existing_custom_config_client_directory_keeps_legacy_default(tmp_path):
    path = tmp_path / 'custom-profile/config/client/game.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'executable': str(tmp_path / 'game.exe')}))
    assert load_config(path).data_dir == tmp_path / 'custom-profile/.state'
