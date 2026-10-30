import test from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import {syncBuiltinESMExports} from 'node:module';
import {EventEmitter} from 'node:events';
import {PassThrough, Writable} from 'node:stream';
import {resolve} from 'node:path';
import {apply} from '../dist/index.js';

// Exercise plugin registration through the actual WorkerClient JSON-RPC transport,
// substituting only the OS process so no game or production database is opened.
test('relocated plugin starts backend workers and finds agent skills', async t => {
  const launches=[];
  t.mock.method(childProcess, 'spawn', (command,args,options) => {
    launches.push({command,args,cwd:options.cwd});
    const child=new EventEmitter();
    child.stdout=new PassThrough();
    child.stderr=new PassThrough();
    child.stdin=new Writable({
      write(chunk,_encoding,done) {
        const request=JSON.parse(chunk.toString());
        child.stdout.write(JSON.stringify({id:request.id,result:{method:request.method}})+'\n');
        done();
      },
      final(done) {
        child.stdout.end();
        child.stderr.end();
        queueMicrotask(()=>child.emit('close',0));
        done();
      },
    });
    child.kill=()=>child.emit('close',0);
    return child;
  });
  syncBuiltinESMExports();
  t.after(()=>{t.mock.restoreAll();syncBuiltinESMExports();});
  const root=resolve('fixture project');
  async function exercise(overrides={}) {
    const registered=new Map();
    let dispose;
    await apply({
      tools:{register:tool=>registered.set(tool.name,tool)},
      skills:{snapshot:async()=>({skills:[],complete:true})},
      systemPrompt:{section:()=>{}},
      effect:fn=>{dispose=fn();},
    },{rootDir:root,...overrides});
    try {
      assert.equal((await registered.get('sgz_game_status').execute({})).method,'game_status');
      assert.equal((await registered.get('sgz_knowledge_status').execute({})).method,'knowledge_status');
      const skills=await registered.get('sgz_skills_list').execute({});
      assert.equal(skills.directory,resolve(root,'agent/skills'));
    } finally { await dispose(); }
  }
  await exercise();
  assert.deepEqual(launches[0],{
    command:resolve(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python'),
    args:['-m','sgz_agent.client.cli','serve','--config',resolve(root,'backend/config/client/game.local.json')],
    cwd:root,
  });
  assert.deepEqual(launches[1],{
    command:resolve(root,process.platform==='win32'?'.venv/Scripts/python.exe':'.venv/bin/python'),
    args:['-m','sgz_agent.knowledge.cli','serve','--directory',resolve(root,'.state/knowledge')],
    cwd:root,
  });
  await exercise({pythonPath:'custom-python',configPath:'custom-config.json',autoStart:false});
  assert.equal(launches[2].command,'custom-python');
  assert.equal(launches[2].args.at(-1),'custom-config.json');
  assert.equal(launches[3].command,'custom-python');
});
