import test from 'node:test';
import assert from 'node:assert/strict';
import {resolve} from 'node:path';
import {apply} from '../dist/index.js';

async function setup(skills={snapshot:async()=>({skills:[],complete:true}),get:async()=>undefined}) {
  const registered=[],sections=[];
  await apply({tools:{register:t=>registered.push(t)},skills,
    systemPrompt:{section:s=>sections.push(s)},effect:()=>{}},
    {rootDir:process.cwd(),autoStart:false});
  return {get:name=>registered.find(t=>t.name===`sgz_${name}`), sections};
}

test('map and roster tools distinguish fresh acquisition from cached reads',async()=>{
  const {get}=await setup();
  assert.ok(get('search_map'));
  assert.deepEqual(get('search_map').parameters.required,['world','x0','y0','x1','y1']);
  for(const name of ['search_map','query_roster','skills_list','skill_load'])
    assert.equal(get(name).isConcurrencySafe(),true,name);
  for(const name of ['scan_region','refresh_roster'])assert.equal(get(name).isConcurrencySafe(),false,name);
});

test('protocol readiness is read-only and scan source defaults to api',async()=>{
  const {get}=await setup();
  assert.equal(get('protocol_status').isConcurrencySafe(),true);
  assert.deepEqual(get('scan_region').parameters.properties.source,
    {type:'string',enum:['api','ui'],default:'api'});
});

test('generic combat assessment accepts arbitrary compositions and never binds to a target name',async()=>{
  const {get}=await setup();
  const tool=get('assess_combat');
  assert.ok(tool);
  assert.equal(tool.isConcurrencySafe(),true);
  assert.deepEqual(tool.parameters.required,['phase']);
  assert.equal(tool.parameters.properties.heroes.items.properties.id.type,'string');
  assert.equal(tool.parameters.properties.context.properties.target_profile.type,'string');
  assert.equal(tool.parameters.properties.min_total_troops.default,0);
});

test('multi-army hunt tools separate read status from persistent commands',async()=>{
  const {get}=await setup();
  assert.ok(get('hunt_start'));
  assert.deepEqual(get('hunt_start').parameters.required,['world','request_id']);
  assert.equal(get('hunt_status').isConcurrencySafe(),true);
  for(const name of ['hunt_start','hunt_stop','hunt_resume'])assert.equal(get(name).isConcurrencySafe(),false);
});

test('single-round dispatch exposes fixed managed operations and separate journal status',async()=>{
  const {get,sections}=await setup();
  const roster=get('hunt_roster_status'),start=get('hunt_dispatch_start'),status=get('hunt_dispatch_status');
  assert.ok(roster);assert.ok(start);assert.ok(status);
  assert.deepEqual(roster.parameters.required,[]);
  assert.deepEqual(Object.keys(roster.parameters.properties),[]);
  assert.deepEqual(start.parameters.required,['world','request_id']);
  assert.deepEqual(Object.keys(start.parameters.properties).sort(),['army_ids','request_id','world']);
  assert.equal(start.parameters.properties.army_ids.maxItems,50);
  assert.deepEqual(status.parameters.required,[]);
  assert.deepEqual(Object.keys(status.parameters.properties),['request_id']);
  assert.equal(roster.isConcurrencySafe(),false);
  assert.equal(start.isConcurrencySafe(),false);
  assert.equal(status.isConcurrencySafe(),true);
  assert.match(start.description,/单轮/);
  assert.ok(sections.some(s=>s.text.includes('sgz_hunt_dispatch_start')));
});

test('replenishment exposes only full API refill and a read-only preview',async()=>{
  const {get,sections}=await setup();const tool=get('replenish');
  assert.ok(tool);assert.equal(tool.isConcurrencySafe(),false);
  assert.deepEqual(tool.parameters.required,['world','request_id']);
  assert.equal(tool.parameters.properties.dry_run.default,false);
  assert.equal(tool.parameters.properties.army_ids.maxItems,50);
  assert.equal(tool.parameters.properties.hero_troop_list,undefined);
  assert.equal(tool.parameters.properties.source,undefined);
  assert.ok(sections.some(s=>s.text.includes('sgz_replenish')));
});

test('replenishment reconciliation accepts an existing command and never arbitrary game payloads',async()=>{
  const {get}=await setup(),tool=get('replenish_reconcile');
  assert.ok(tool);
  assert.equal(tool.isConcurrencySafe(),false);
  assert.deepEqual(tool.parameters.required,['world','command_id']);
  assert.deepEqual(Object.keys(tool.parameters.properties).sort(),['command_id','world']);
});

test('project skills list/load preserve native invocation policy and body isolation',async()=>{
  const catalog=[
    {name:'sgz-read',description:'read maps',provider:'sgz-filesystem',invocation:{modelInvocable:true}},
    {name:'sgz-disabled',description:'hidden',provider:'sgz-filesystem',invocation:{modelInvocable:false}},
    {name:'unrelated',description:'unrelated',provider:'filesystem',invocation:{modelInvocable:true}}];
  const {get}=await setup({snapshot:async()=>({skills:catalog,complete:true}),
    get:async name=>({...catalog.find(s=>s.name===name),content:'instructions',resourceBase:{kind:'directory',path:resolve('agent/skills/conversation/sgz-read')}})});
  const result=await get('skills_list').execute({});
  assert.deepEqual(result.skills.map(s=>s.name),['sgz-read']);
  assert.equal(result.skills[0].content,undefined);
  assert.equal((await get('skill_load').execute({name:'sgz-read'})).content,'instructions');
  await assert.rejects(get('skill_load').execute({name:'sgz-disabled'}),/skill_not_available/);
  await assert.rejects(get('skill_load').execute({name:'unrelated'}),/skill_not_available/);
  await assert.rejects(get('skill_load').execute({name:'../secrets'}),/invalid_skill_name/);
});

test('a skill disabled between discovery and loading is rejected',async()=>{
  const {get}=await setup({snapshot:async()=>({complete:true,skills:[{
    name:'sgz-read',provider:'sgz-filesystem',invocation:{modelInvocable:true}}]}),
    get:async()=>({name:'sgz-read',provider:'sgz-filesystem',invocation:{modelInvocable:false},content:'secret'})});
  await assert.rejects(get('skill_load').execute({name:'sgz-read'}),/skill_not_available/);
});
