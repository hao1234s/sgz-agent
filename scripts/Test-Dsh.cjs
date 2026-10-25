// Run with the installed DSH Electron binary and ELECTRON_RUN_AS_NODE=1.
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');
const assert = require('node:assert/strict');

(async () => {
  const root = path.resolve(__dirname, '..');
  const runtime = path.join(path.dirname(process.execPath), 'resources', 'app.asar', 'dsh', 'package.json');
  const r = createRequire(runtime);
  const {boot, composeEntries, bundlePatchPaths} = await import(pathToFileURL(r.resolve('@deepseek-ai/dsh-app-boot')).href);
  const yaml = r('js-yaml');
  const installed = process.argv.includes('--installed');
  const packageRoot = installed ? path.dirname(createRequire(path.join(process.env.USERPROFILE,'.dsh','profiles','desktop','package.json')).resolve('dsh-sgz-agent/package.json')) : path.join(root,'plugin');
  const manifest = JSON.parse(fs.readFileSync(path.join(packageRoot,'package.json'),'utf8'));
  const patch = bundlePatchPaths(packageRoot,manifest.dsh.bundle).flatMap(file=>yaml.load(fs.readFileSync(file,'utf8')));
  const entries = composeEntries([[{insert:[
    {id:'system-prompt',name:'@deepseek-ai/dsh-system-prompt'},
    {id:'tools',name:'@deepseek-ai/dsh-tools'},
    {id:'skills',name:'@deepseek-ai/dsh-skill'},
  ]}],patch]);
  const agent = entries.find(row=>row.id==='sgz-agent');
  if (!agent) throw Error('bundle_patch_did_not_insert_plugin');
  agent.name = pathToFileURL(path.join(packageRoot,'dist','index.js')).href;
  agent.config.rootDir = root;
  const directory = path.join(root,'.state','dsh-smoke');
  fs.mkdirSync(directory,{recursive:true});
  const skillDirectory=fs.mkdtempSync(path.join(directory,'skills-'));
  const skillFile=path.join(skillDirectory,'sgz-test','SKILL.md');
  fs.mkdirSync(path.dirname(skillFile));
  const writeSkill=body=>fs.writeFileSync(skillFile,'---\nname: sgz-test\ndescription: Local integration fixture\n---\n'+body);
  writeSkill('first fixture body');
  fs.mkdirSync(path.join(skillDirectory,'sgz-disabled'));
  fs.writeFileSync(path.join(skillDirectory,'sgz-disabled','SKILL.md'),
    '---\nname: sgz-disabled\ndescription: Disabled fixture\ndisable-model-invocation: true\n---\nHidden fixture body');
  const provider=entries.find(row=>row.id==='sgz-skills');
  if(!provider)throw Error('skill_provider_missing');
  provider.config.customSkillDirs=[skillDirectory];
  const configPath = path.join(directory,'cordis.yml');
  fs.writeFileSync(configPath,JSON.stringify(entries,null,2));
  const ctx = await boot('sgz-smoke',configPath,[],undefined,pathToFileURL(runtime).href);
  try {
    const names = ['game_status','prepare_login','observe_map','inspect_target','explore_region','pause_task','resume_task','export_results','knowledge_search','knowledge_status',
      'scan_region','search_map','refresh_roster','query_roster','skills_list','skill_load','protocol_status','assess_combat',
      'hunt_start','hunt_status','hunt_stop','hunt_resume'];
    for(const name of names)if(!ctx.tools.get(`sgz_${name}`))throw Error(`tool_missing:${name}`);
    const status = await ctx.tools.get('sgz_game_status').execute({});
    if(!Array.isArray(status.windows))throw Error('invalid_worker_status');
    assert.equal(status.service.state,'running');
    const protocol=await ctx.tools.get('sgz_protocol_status').execute({});
    assert.equal(protocol.api_scan_available,true);
    assert.equal(protocol.world_bandits_complete,false);
    const scan=await ctx.tools.get('sgz_scan_region').execute({request_id:`api-smoke-${Date.now()}`,world:status.world,
      x0:582,y0:818,x1:601,y1:837});
    let apiJob;
    for(let n=0;n<100;n++){
      apiJob=(await ctx.tools.get('sgz_game_status').execute({job_id:scan.id})).jobs[0];
      if(apiJob.status!=='running')break;
      await new Promise(resolve=>setTimeout(resolve,100));
    }
    assert.equal(apiJob.status,'complete',apiJob.reason);
    const live=await ctx.tools.get('sgz_search_map').execute({world:status.world,x0:592,y0:830,x1:592,y1:830,
      kind:'resource',resource:'铁矿',min_level:4,max_level:4});
    assert.equal(live.results[0].source,'client_api');
    assert.equal(live.results[0].level,4);
    console.log(JSON.stringify({api_scan:apiJob.reason,resource:live.results[0]}));
    const catalog=await ctx.tools.get('sgz_skills_list').execute({});
    assert.equal(catalog.complete,true);
    assert.deepEqual(catalog.skills.map(s=>s.name),['sgz-test']);
    const readSkill=()=>ctx.tools.get('sgz_skill_load').execute({name:'sgz-test'});
    assert.equal((await readSkill()).content,'first fixture body');
    await assert.rejects(ctx.tools.get('sgz_skill_load').execute({name:'sgz-disabled'}),/skill_not_available/);
    writeSkill('updated fixture body');
    assert.equal((await readSkill()).content,'updated fixture body');
    const added=path.join(skillDirectory,'sgz-added');
    fs.mkdirSync(added);
    fs.writeFileSync(path.join(added,'SKILL.md'),'---\nname: sgz-added\ndescription: Added fixture\n---\nNew fixture');
    let refreshed=[];
    const deadline=Date.now()+5000;
    do {
      await new Promise(resolve=>setTimeout(resolve,100));
      refreshed=(await ctx.tools.get('sgz_skills_list').execute({})).skills;
    } while(!refreshed.some(s=>s.name==='sgz-added')&&Date.now()<deadline);
    assert.ok(refreshed.some(s=>s.name==='sgz-added'),'native skill watcher must discover newly added skills');
    const map=await ctx.tools.get('sgz_search_map').execute({world:status.world,x0:591,y0:830,x1:593,y1:832,include_stale:true});
    assert.ok(Array.isArray(map.results));
    assert.equal(map.coverage.total_tiles,9);
    const roster=await ctx.tools.get('sgz_query_roster').execute({world:status.world});
    assert.ok(Array.isArray(roster.armies));
    const knowledge = await ctx.tools.get('sgz_knowledge_status').execute({});
    if(!Number.isInteger(knowledge.records))throw Error('invalid_knowledge_status');
    const search = await ctx.tools.get('sgz_knowledge_search').execute({query:'曹操',limit:1});
    if(!Array.isArray(search.results))throw Error('invalid_knowledge_results');
    if(knowledge.records>0 && search.results[0]?.name!=='曹操')throw Error('knowledge_exact_match_failed');
    console.log(JSON.stringify({ok:true,installed,registeredTools:names.length,windows:status.windows.length,calibrated:status.calibrated,
      mapObservedTiles:map.coverage.observed_tiles,rosterObserved:roster.observed,skillsVerified:['discover','load','disabled','body-update','watch-new-file'],
      knowledgeRecords:knowledge.records,knowledgeVectors:knowledge.vectors,knowledgeQuery:search.results[0]?.name}));
  } finally {
    await ctx.fiber.dispose();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
