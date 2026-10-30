import importlib
import io
import json
import sys
import pytest


def test_knowledge_legacy_imports_share_implementation(monkeypatch, capsys, tmp_path):
    old = importlib.import_module('sgz_agent.knowledge_cli')
    new = importlib.import_module('sgz_agent.knowledge.cli')
    assert old is new
    from sgz_agent.knowledge import KnowledgeBase
    from sgz_agent.knowledge.store import KnowledgeBase as implementation
    assert KnowledgeBase is implementation
    class Fake:
        def __init__(self, directory): assert directory == str(tmp_path)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def status(self): return {'fixture': True}
    monkeypatch.setattr(old, 'KnowledgeBase', Fake)
    monkeypatch.setattr(sys, 'argv', ['sgz-knowledge', 'status', '--directory', str(tmp_path)])
    assert new.main() == 0
    assert json.loads(capsys.readouterr().out) == {'fixture': True}
    assert importlib.import_module('sgz_agent.knowledge_sources') is importlib.import_module('sgz_agent.knowledge.sources')


@pytest.mark.parametrize('name', ['cli', 'config', 'windows', 'client_api', 'hunt_policy'])
def test_client_legacy_modules_share_globals(name, monkeypatch):
    old = importlib.import_module('sgz_agent.' + name)
    new = importlib.import_module('sgz_agent.client.' + name)
    assert old is new
    monkeypatch.setattr(old, '_isolation_fixture', 42, raising=False)
    assert new._isolation_fixture == 42


def test_legacy_knowledge_globals_still_control_constructor(monkeypatch, tmp_path):
    from sgz_agent import knowledge
    class Replacement:
        name = 'legacy-patched-embedding'
        dimensions = 3
        def __init__(self, directory): self.directory = directory
    monkeypatch.setattr(knowledge, 'ChineseEmbeddings', Replacement)
    with knowledge.KnowledgeBase(tmp_path) as kb:
        assert isinstance(kb.embedder, Replacement)
