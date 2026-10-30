// Boots only the offline tool/skill services in a temporary profile. No game or model calls.
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,cpSync,mkdirSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {parseArgs} from 'node:util';
import {renderProfile} from './Render-Profile.mjs';
const {values}=parseArgs({options:{runtime:{type:'string'},python:{type:'string'}}});
if(!values.runtime)throw Error('--runtime must name the package.json in a DSH runtime');
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
const directory=mkdtempSync(join(tmpdir(),'sgz-profile-test-'));
const runtime=resolve(values.runtime),requireRuntime=createRequire(runtime);
const {boot,composeEntries}=await import(pathToFileURL(requireRuntime.resolve('@deepseek-ai/dsh-app-boot')).href);
try {
  mkdirSync(join(directory,'agent'),{recursive:true});
  cpSync(join(root,'agent/skills'),join(directory,'agent/skills'),{recursive:true});
  for(const scenario of ['chat','knowledge','local','chat-stale-provider','chat-stale-empty']){
    const profile=scenario.startsWith('chat-stale')?'chat':scenario;
    const patches=renderProfile({profile,rootDir:directory,overrides:{autoStart:false,
      pythonPath:values.python??'deliberately-missing-python',
      ...(scenario==='chat-stale-empty'?{capabilities:{}}:{})}});
    const entries=composeEntries([[{insert:[
      {id:'system-prompt',name:'@deepseek-ai/dsh-system-prompt'},
      {id:'tools',name:'@deepseek-ai/dsh-tools'},
      {id:'skills',name:'@deepseek-ai/dsh-skill'},
    ]}],patches]);
    entries.find(x=>x.id==='sgz-agent').name=pathToFileURL(join(root,'agent/plugin/dist/index.js')).href;
    entries.find(x=>x.id==='sgz-skills').config.watch=false;
    if(scenario.startsWith('chat-stale')) entries.find(x=>x.id==='sgz-skills').config.customSkillDirs=[join(directory,'agent/skills/conversation'),join(directory,'agent/skills/client')];
    const config=join(directory,scenario+'.json'),serialized=JSON.stringify(entries);writeFileSync(config,serialized);
    const ctx=await boot('sgz-profile-test',config,[],undefined,pathToFileURL(runtime).href);
    try {
      assert.equal(!!ctx.tools.get('sgz_assess_combat'),scenario!=='chat-stale-empty','async plugin must finish registering before boot resolves');
      for(const entry of ctx.loader.entries())if(['sgz-agent','sgz-skills'].includes(entry.options.id))await entry.fiber.await();
      assert.equal(!!ctx.tools.get('sgz_game_status'),profile==='local');
      assert.equal(!!ctx.tools.get('sgz_knowledge_search'),profile!=='chat');
      const lookup={cwd:directory};
      const native=await ctx.skills.snapshot(lookup);
      const catalog=await ctx.tools.get('sgz_skills_list').execute({});
      const expected=scenario==='chat-stale-empty'?[]:profile==='local'?['sgz-combat-assessment','sgz-giant-halberdier','sgz-multi-army-hunt']:['sgz-combat-assessment'];
      assert.deepEqual(native.skills.filter(s=>s.provider==='sgz-filesystem').map(s=>s.name).sort(),expected);
      assert.deepEqual(catalog.skills.map(s=>s.name).sort(),expected);
      for(const name of expected){const loaded=await ctx.tools.get('sgz_skill_load').execute({name});assert.ok(loaded.content);}
      if(profile!=='local')assert.equal(await ctx.skills.get('sgz-multi-army-hunt',lookup),undefined);
      if(values.python&&scenario!=='chat-stale-empty'){
        assert.equal((await ctx.tools.get('sgz_assess_combat').execute({phase:'draw'})).decision,'wait');
        if(profile==='knowledge')assert.equal((await ctx.tools.get('sgz_knowledge_status').execute({})).records,0);
      }
      assert.equal(readFileSync(config,'utf8'),serialized,'normalization must not rewrite host configuration');
      console.log(scenario+': native skills, tools and async assembly passed');
    } finally {await ctx.fiber.dispose();}
  }
} finally {rmSync(directory,{recursive:true,force:true});}
