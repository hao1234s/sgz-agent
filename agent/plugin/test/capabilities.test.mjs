import test from 'node:test';
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import {syncBuiltinESMExports} from 'node:module';
import {EventEmitter} from 'node:events';
import {PassThrough,Writable} from 'node:stream';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {apply} from '../dist/index.js';

async function setup(config={},skills) {
  const tools=new Map(),sections=[],disposers=[];
  await apply({tools:{register:t=>tools.set(t.name,t)},systemPrompt:{section:s=>sections.push(s)},
    skills:skills??{snapshot:async()=>({skills:[],complete:true}),get:async()=>undefined},
    effect:fn=>{disposers.push(fn());}}, {rootDir:process.cwd(),autoStart:false,...config});
  return {tools,sections,close:()=>Promise.allSettled(disposers.map(f=>f()))};
}
function fakeProcesses(t,{failClient=false,failKnowledge=false}={}) {
  const launches=[];
  t.mock.method(childProcess,'spawn',(command,args,options)=>{
    launches.push({command,args,cwd:options.cwd});
    const child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();
    child.stdin=new Writable({write(chunk,enc,done){
      const req=JSON.parse(chunk.toString());
      const error=(failClient&&args.includes('sgz_agent.client.cli')) || (failKnowledge&&args.includes('sgz_agent.knowledge.cli'));
      child.stdout.write(JSON.stringify({id:req.id,...(error?{error:{message:'fixture_failure'}}:{result:{method:req.method}})})+'\n');done();
    },final(done){child.stdout.end();child.stderr.end();queueMicrotask(()=>child.emit('close',0));done();}});
    child.kill=()=>child.emit('close',0);return child;
  });syncBuiltinESMExports();t.after(()=>{t.mock.restoreAll();syncBuiltinESMExports();});return launches;
}
test('legacy configuration preserves every tool input and concurrency contract',async()=>{
  const app=await setup();const expected=JSON.parse(readFileSync(new URL('./fixtures/legacy-tools.json',import.meta.url)));
  const actual=[...app.tools.values()].map(t=>({name:t.name,parameters:t.parameters,concurrency:t.isConcurrencySafe()}));
  assert.deepEqual(actual.sort((a,b)=>a.name.localeCompare(b.name)),expected.sort((a,b)=>a.name.localeCompare(b.name)));await app.close();
});
test('all eight explicit capability combinations register only selected tools and prompts',async()=>{
  for(let bits=0;bits<8;bits++){
    const capabilities={analysis:!!(bits&1),knowledge:!!(bits&2),client:!!(bits&4)};
    const app=await setup({capabilities});
    for(const [cap,name] of [['analysis','sgz_assess_combat'],['knowledge','sgz_knowledge_search'],['client','sgz_game_status']]) assert.equal(app.tools.has(name),capabilities[cap]);
    assert.ok(app.tools.has('sgz_skills_list'));
    const text=app.sections.map(s=>s.text).join('\n');
    if(!capabilities.client)assert.doesNotMatch(text,/sgz_game_status|sgz_hunt_start|sgz_replenish/);
    if(!capabilities.knowledge)assert.doesNotMatch(text,/sgz_knowledge_search/);
    if(!capabilities.analysis)assert.doesNotMatch(text,/sgz_assess_combat/);
    await app.close();
  }
});
test('chat is Python-free until the first pure calculation, even with autoStart',async t=>{
  const launches=fakeProcesses(t);
  const app=await setup({capabilities:{analysis:true},autoStart:true,analysis:{pythonPath:'analysis-python'}});
  assert.equal(launches.length,0);await app.tools.get('sgz_skills_list').execute({});assert.equal(launches.length,0);
  await app.tools.get('sgz_assess_combat').execute({phase:'draw'});
  assert.equal(launches.length,1);assert.equal(launches[0].command,'analysis-python');
  assert.deepEqual(launches[0].args,['-m','sgz_agent.analysis.cli','serve']);await app.close();
});
test('client startup failure does not block calculation, skills or knowledge',async t=>{
  fakeProcesses(t,{failClient:true});const app=await setup({autoStart:true});
  assert.equal((await app.tools.get('sgz_game_status').execute({})).service.state,'failed');
  for(const name of ['sgz_assess_combat','sgz_knowledge_status'])assert.ok(await app.tools.get(name).execute({}));
  assert.ok(await app.tools.get('sgz_skills_list').execute({}));await app.close();
});
test('knowledge failure does not block the client',async t=>{
  fakeProcesses(t,{failKnowledge:true});const app=await setup();
  await assert.rejects(app.tools.get('sgz_knowledge_status').execute({}),/fixture_failure/);
  assert.ok(await app.tools.get('sgz_game_status').execute({}));await app.close();
});
test('invalid capability values never fall back to full access',async()=>{
  for(const capabilities of [null,[],true,{client:'false'},{unknown:true}]) await assert.rejects(setup({capabilities}),/capabilit/);
});

test('module autoStart overrides legacy autoStart without starting either optional worker',async t=>{
  const launches=fakeProcesses(t);
  const app=await setup({autoStart:true,client:{autoStart:false}});
  assert.equal(launches.length,0);
  await app.tools.get('sgz_game_status').execute({});
  assert.equal(launches.length,1);
  assert.ok(launches[0].args.includes('sgz_agent.client.cli'));
  await app.close();
});
