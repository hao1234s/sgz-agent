"""Check real import direction, including legacy aliases and lazy imports."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parents[1] / 'src/sgz_agent'
CLIENT = {p.stem for p in (ROOT / 'client').glob('*.py') if p.stem != '__init__'}

def imports(code, module):
    package = module.rpartition('.')[0]
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Import):
            yield from (x.name for x in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = importlib.util.resolve_name('.' * node.level + (node.module or ''), package) if node.level else (node.module or '')
            yield base
            yield from (base + '.' + x.name for x in node.names)

def capability(name):
    parts = name.split('.')
    if len(parts) < 2 or parts[0] != 'sgz_agent': return None
    if parts[1] in {'core','analysis','client','knowledge'}: return parts[1]
    if parts[1] in CLIENT or parts[1] == 'rpc': return 'client'
    if parts[1] in {'knowledge_cli','knowledge_sources'}: return 'knowledge'
    if parts[1] == 'combat_assessment': return 'analysis'

def violations(code, module, owner):
    allowed={'core':{'core'},'analysis':{'core','analysis'},
             'knowledge':{'core','knowledge'},'client':{'core','client','analysis'}}[owner]
    result=[]
    for name in imports(code,module):
        cap=capability(name)
        if cap and cap not in allowed: result.append(name)
        elif owner in {'core','analysis'} and name.split('.')[0] not in sys.stdlib_module_names | {'sgz_agent'}:
            result.append(name)
    return result

def test_architecture_checker_detects_lazy_and_legacy_reverse_imports():
    assert violations('def f():\n from sgz_agent import service', 'sgz_agent.core.rpc', 'core')
    assert violations('from ..knowledge_cli import main', 'sgz_agent.client.cli','client')
    assert violations('import PIL', 'sgz_agent.analysis.service','analysis')

def test_capability_imports_follow_boundaries():
    for owner in ['core','analysis','client','knowledge']:
        for p in (ROOT/owner).rglob('*.py'):
            module='sgz_agent.'+'.'.join(p.relative_to(ROOT).with_suffix('').parts)
            assert not violations(p.read_text(),module,owner), str(p)

def test_knowledge_rpc_blocks_all_client_and_desktop_imports(tmp_path):
    code='''
import importlib.abc, sys
blocked = ('sgz_agent.client','sgz_agent.config','sgz_agent.service','sgz_agent.lease',
           'sgz_agent.windows','PIL','mss','paddleocr','frida')
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == x or fullname.startswith(x + '.') for x in blocked):
            raise AssertionError('forbidden_import:' + fullname)
sys.meta_path.insert(0,Block())
from sgz_agent.knowledge.cli import serve
serve(sys.argv[1])
'''
    requests=[dict(jsonrpc='2.0',id=1,method='knowledge_status',params={}),
              dict(jsonrpc='2.0',id=2,method='knowledge_search',params={'query':'曹操'})]
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path/'knowledge')],cwd=tmp_path,
        env={**os.environ,'PYTHONPATH':str(ROOT.parent)},input='\n'.join(map(json.dumps,requests))+'\n',
        text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    rows=list(map(json.loads,result.stdout.splitlines()))
    assert rows[0]['result']['records']==0
    assert rows[1]['result']['results']==[]
