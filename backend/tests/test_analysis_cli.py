"""The calculation process must work with no desktop or knowledge environment."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_analysis_worker_never_loads_optional_capabilities(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'src'
    script = '''
import importlib.abc, runpy, sys
blocked = ('sgz_agent.client', 'sgz_agent.service', 'sgz_agent.config',
           'sgz_agent.lease', 'sgz_agent.windows', 'sgz_agent.knowledge',
           'PIL', 'mss', 'paddleocr', 'frida', 'fastembed', 'qdrant_client')
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == x or fullname.startswith(x + '.') for x in blocked):
            raise AssertionError('forbidden_import:' + fullname)
sys.meta_path.insert(0, Block())
sys.argv = ['analysis', 'serve']
runpy.run_module('sgz_agent.analysis.cli', run_name='__main__')
'''
    requests = [dict(jsonrpc='2.0', id=i, method=method, params=params)
                for i, method, params in [(1, 'assess_combat', {'phase': 'draw'}),
                                          (2, 'game_status', {}),
                                          (3, 'assess_combat', {'phase': 'invalid'})]]
    result = subprocess.run([sys.executable, '-S', '-c', script], cwd=tmp_path,
                            env={**os.environ, 'PYTHONPATH': str(source)},
                            input='\n'.join(map(json.dumps, requests)) + '\n',
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    rows = {row['id']: row for row in map(json.loads, result.stdout.splitlines())}
    assert rows[1]['result']['decision'] == 'wait'
    assert rows[2]['error']['message'] == 'unknown_method'
    assert 'error' in rows[3]
    assert not list(tmp_path.iterdir())


def test_old_combat_import_is_same_function():
    from sgz_agent.combat_assessment import assess_combat as old
    from sgz_agent.analysis.combat_assessment import assess_combat as new
    assert old is new
