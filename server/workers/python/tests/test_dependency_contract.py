from pathlib import Path
import tomllib


def test_base_install_has_no_optional_runtime_dependencies():
    project = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text())['project']
    assert project['dependencies'] == []
    extras = project['optional-dependencies']
    assert any(x.startswith('mss==') for x in extras['desktop'])
    assert any(x.startswith('Pillow==') for x in extras['desktop'])
    assert any(x.startswith('numpy') for x in extras['ocr'])
    assert any(x.startswith('portalocker') for x in extras['knowledge'])
    assert not any('frida' in x for x in extras['knowledge'])
