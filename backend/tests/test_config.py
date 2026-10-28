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
