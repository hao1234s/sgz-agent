import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {resolveConfig} from '../dist/core/config.js';
const helper=name=>fileURLToPath(new URL('./helpers/'+name,import.meta.url));
test('disabled modules are not imported in any capability combination',()=>{
  for(let bits=0;bits<8;bits++){
    const capabilities={analysis:!!(bits&1),knowledge:!!(bits&2),client:!!(bits&4)};
    const blocked=Object.keys(capabilities).filter(x=>!capabilities[x]);
    const result=spawnSync(process.execPath,['--no-warnings','--loader',helper('block-capabilities.mjs'),helper('isolation-runner.mjs')],
      {encoding:'utf8',timeout:10000,env:{...process.env,SGZ_CAPABILITIES:JSON.stringify(capabilities),SGZ_FORBIDDEN:blocked.join(',')}});
    assert.equal(result.status,0,result.stderr);
    assert.ok(JSON.parse(result.stdout).includes('sgz_skills_list'));
  }
});
test('interpreters resolve independently by capability and platform without touching configuration files',()=>{
  const input={rootDir:'/tmp/project',pythonPath:'legacy',analysis:{pythonPath:'calc'},knowledge:{pythonPath:'kb'}};
  const config=resolveConfig(input,'linux');
  assert.equal(config.analysis.pythonPath,'calc');assert.equal(config.client.pythonPath,'legacy');assert.equal(config.knowledge.pythonPath,'kb');
  assert.match(resolveConfig({rootDir:'/tmp/project'},'win32').analysis.pythonPath,/Scripts\/python.exe$/);
  assert.match(resolveConfig({rootDir:'/tmp/project'},'darwin').analysis.pythonPath,/bin\/python$/);
  const disabled={rootDir:'/tmp/project',capabilities:{}};
  Object.defineProperty(disabled,'client',{get(){throw Error('read_disabled_configuration');}});
  assert.equal(resolveConfig(disabled).client.autoStart,false);
});
