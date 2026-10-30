import test from 'node:test';
import assert from 'node:assert/strict';
import {resolve} from 'node:path';
import {renderProfile} from '../../scripts/Render-Profile.mjs';
import {apply} from '../dist/index.js';

test('profiles configure tools and native skill roots from the same capability selection',()=>{
  const rootDir=resolve('fixture with spaces');
  for(const profile of ['chat','knowledge','local']){
    const [{insert}]=renderProfile({profile,rootDir});
    const agent=insert.find(x=>x.id==='sgz-agent'),provider=insert.find(x=>x.id==='sgz-skills');
    assert.deepEqual(agent.config.capabilities,{analysis:true,client:profile==='local',knowledge:profile!=='chat'});
    assert.equal(agent.config.rootDir,rootDir);assert.equal(provider.config.providerName,'sgz-filesystem');
    assert.equal(provider.config.customSkillDirs.some(x=>x.endsWith('/client')),profile==='local');
    assert.ok(provider.config.customSkillDirs.every(x=>x.startsWith(rootDir)));
  }
  const [{insert}]=renderProfile({profile:'chat',rootDir,overrides:{capabilities:{}}});
  assert.deepEqual(insert[0].config.customSkillDirs,[]);
  assert.throws(()=>renderProfile({profile:'../secret',rootDir}),/invalid_profile/);
});

test('explicit chat rejects client skills in a stale provider on list and load',async()=>{
  const root=process.cwd(),registered=new Map();
  const skill=(name,directory)=>({name,provider:'sgz-filesystem',invocation:{modelInvocable:true},resourceBase:{kind:'directory',path:resolve(root,'agent/skills',directory,name)},content:'body'});
  const catalog=[skill('sgz-combat-assessment','conversation'),skill('sgz-multi-army-hunt','client')];
  await apply({tools:{register:t=>registered.set(t.name,t)},systemPrompt:{section:()=>{}},effect:()=>{},
    skills:{snapshot:async()=>({skills:catalog,complete:true}),get:async name=>catalog.find(s=>s.name===name)}},
    {rootDir:root,capabilities:{analysis:true}});
  const result=await registered.get('sgz_skills_list').execute({});
  assert.deepEqual(result.skills.map(s=>s.name),['sgz-combat-assessment']);
  assert.deepEqual(result.directories,[resolve(root,'agent/skills/conversation')]);
  await assert.rejects(registered.get('sgz_skill_load').execute({name:'sgz-multi-army-hunt'}),/skill_not_available/);
  assert.equal((await registered.get('sgz_skill_load').execute({name:'sgz-combat-assessment'})).content,'body');
});

test('local can keep every capability enabled while disabling client autostart',()=>{
  const [{insert}]=renderProfile({profile:'local',rootDir:process.cwd(),overrides:{client:{autoStart:false}}});
  const config=insert.find(x=>x.id==='sgz-agent').config;
  assert.deepEqual(config.capabilities,{analysis:true,client:true,knowledge:true});
  assert.equal(config.client.autoStart,false);
});
