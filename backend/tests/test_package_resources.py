from importlib.resources import files
import json
from pathlib import Path
import tomllib


def test_client_javascript_resources_are_packaged_together():
    config = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text())
    assets = config['tool']['setuptools']['package-data']['sgz_agent.client']
    assert len(assets) == 9
    for name in assets:
        assert files('sgz_agent.client').joinpath(name).read_text(encoding='utf-8')


def test_builtin_knowledge_seed_matches_external_config():
    bundled = json.loads(files('sgz_agent.knowledge').joinpath('data/knowledge-mechanics.json').read_text())
    external = json.loads((Path(__file__).parents[1] / 'config/knowledge-mechanics.json').read_text())
    assert bundled == external
