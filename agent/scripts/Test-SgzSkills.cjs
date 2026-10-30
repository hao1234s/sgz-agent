// Uses installed DSH, project skills and an isolated worker with a dummy executable.
// Never observes or controls the game or uses its production data directory.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');

(async () => {
  const root = path.resolve(__dirname, '../..');
  const runtime = path.join(path.dirname(process.execPath), 'resources/app.asar/dsh/package.json');
  const requireRuntime = createRequire(runtime);
  const {boot} = await import(pathToFileURL(requireRuntime.resolve('@deepseek-ai/dsh-app-boot')).href);
  const installed = createRequire(path.join(process.env.USERPROFILE, '.dsh/profiles/desktop/package.json'));
  const pluginRoot = path.dirname(installed.resolve('dsh-sgz-agent/package.json'));
  const pluginVersion = JSON.parse(fs.readFileSync(path.join(pluginRoot, 'package.json'), 'utf8')).version;
  assert.equal(pluginVersion, JSON.parse(fs.readFileSync(path.join(root, 'agent/plugin/package.json'), 'utf8')).version,
    'installed plugin must match the workspace release');
  assert.equal(fs.readFileSync(path.join(pluginRoot, 'dist/index.js'), 'utf8'),
    fs.readFileSync(path.join(root, 'agent/plugin/dist/index.js'), 'utf8'),
    'installed workflow prompt must match the current build');
  const directory = path.join(root, '.state/skill-validation');
  fs.mkdirSync(directory, {recursive:true});
  const assessmentConfig = path.join(directory,'assessment-worker.json');
  fs.writeFileSync(assessmentConfig,JSON.stringify({executable:path.join(directory,'nonexistent-game.exe'),
    data_dir:path.join(directory,'assessment-data'),identity:{world:'offline-assessment'}}));
  const config = path.join(directory, 'cordis.yml');
  fs.writeFileSync(config, JSON.stringify([
    {id:'system-prompt', name:'@deepseek-ai/dsh-system-prompt'},
    {id:'tools', name:'@deepseek-ai/dsh-tools'},
    {id:'skills', name:'@deepseek-ai/dsh-skill'},
    {id:'project-skills', name:'@deepseek-ai/dsh-skill-filesystem', config:{
      providerName:'sgz-filesystem', includeDefaultRoots:false,
      customSkillDirs:[path.join(root,'agent/skills/conversation'),path.join(root,'agent/skills/client')], watch:true}},
    {id:'sgz-agent', name:pathToFileURL(path.join(pluginRoot,'dist/index.js')).href,
      config:{rootDir:root,autoStart:false,configPath:assessmentConfig}},
  ], null, 2));
  const ctx = await boot('sgz-skill-validation', config, [], undefined, pathToFileURL(runtime).href);
  try {
    const catalog = await ctx.tools.get('sgz_skills_list').execute({});
    assert.equal(catalog.complete, true);
    const skill = catalog.skills.find(s => s.name === 'sgz-giant-halberdier');
    assert.ok(skill, 'project combat skill must be discoverable');
    assert.equal(skill.content, undefined, 'discovery must not eagerly load instructions');
    const loaded = await ctx.tools.get('sgz_skill_load').execute({name:skill.name});
    assert.ok(loaded.content.trim().length > 0);
    assert.equal(path.resolve(loaded.resourceBase.path), path.join(root, 'agent/skills/client/sgz-giant-halberdier'));
    const source = fs.readFileSync(path.join(loaded.resourceBase.path, 'SKILL.md'), 'utf8');
    const body = source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').trim();
    assert.equal(loaded.content.trim().replace(/\r\n/g, '\n'), body.replace(/\r\n/g, '\n'),
      'native loader must return the current on-disk skill body');
    assert.ok(fs.existsSync(path.join(loaded.resourceBase.path, 'references/mechanics.md')),
      'mechanics reference must be available from the loaded resource base');
    const assessment = catalog.skills.find(s => s.name === 'sgz-combat-assessment');
    assert.ok(assessment, 'generic assessment skill must be discoverable');
    const assessmentBody = await ctx.tools.get('sgz_skill_load').execute({name:assessment.name});
    const assessmentSource = fs.readFileSync(path.join(assessmentBody.resourceBase.path, 'SKILL.md'), 'utf8');
    assert.equal(assessmentBody.content.trim().replace(/\r\n/g,'\n'),
      assessmentSource.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/,'').trim().replace(/\r\n/g,'\n'));
    assert.ok(fs.existsSync(path.join(assessmentBody.resourceBase.path,'references/model.md')));
    const multi = catalog.skills.find(s => s.name === 'sgz-multi-army-hunt');
    assert.ok(multi, 'multi-army skill must be discoverable');
    const multiBody = await ctx.tools.get('sgz_skill_load').execute({name:multi.name});
    const multiSource = fs.readFileSync(path.join(multiBody.resourceBase.path, 'SKILL.md'), 'utf8');
    assert.equal(multiBody.content.trim().replace(/\r\n/g,'\n'),
      multiSource.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/,'').trim().replace(/\r\n/g,'\n'));
    for (const name of ['hunt_start','hunt_status','hunt_stop','hunt_resume']) {
      assert.ok(ctx.tools.get('sgz_'+name), 'hunt tool must be registered: '+name);
      assert.equal(ctx.tools.get('sgz_'+name).isConcurrencySafe(), name === 'hunt_status');
    }
    const huntStatus = await ctx.tools.get('sgz_hunt_status').execute({});
    assert.equal(huntStatus.capabilities.available, false);
    assert.equal(huntStatus.capabilities.mutations_enabled, false);
    assert.ok(huntStatus.capabilities.missing.length > 0);
    assert.deepEqual(huntStatus.campaigns, []);
    assert.ok(ctx.tools.get('sgz_replenish'));
    assert.equal(ctx.tools.get('sgz_replenish').isConcurrencySafe(),false);
    const gameStatus = await ctx.tools.get('sgz_game_status').execute({});
    assert.equal(gameStatus.capabilities.replenish.available,false);
    assert.equal(gameStatus.capabilities.replenish.transport,'api');
    await assert.rejects(ctx.tools.get('sgz_replenish').execute({
      world:'offline-assessment',request_id:'offline-replenish',dry_run:true}),/replenish_identity_missing/);
    assert.equal(ctx.tools.get('sgz_assess_combat').isConcurrencySafe(),true);
    const assess = args => ctx.tools.get('sgz_assess_combat').execute(args);
    assert.equal((await assess({phase:'draw'})).decision,'wait');
    const input={phase:'idle',observation_age_seconds:0,arrival_morale:80,morale_rule_verified:true,
      heroes:['A','B','C'].map((id,i)=>({id,is_main:i===0,troops:9000,capacity:10000})),
      min_total_troops:20000};
    const unknown=await assess(input);
    assert.equal(unknown.decision,'insufficient_evidence');
    assert.equal(unknown.win_probability,null);
    assert.ok(Math.abs(unknown.morale.outgoing_damage_factor-.86)<1e-10);
    const context={ruleset:'fixture-rules',team_build:'fixture-arbitrary-team',
      target_profile:'fixture-three-defenders',defender_waves:3};
    const reports=[1,2,3].map(i=>({id:`fixture-${i}`,source:`fixture:${i}`,context,
      heroes:input.heroes.map(h=>({...h,troops:8000})),arrival_morale:70,
      encounter_complete:true,result:'victory',completed_waves:3}));
    assert.equal((await assess({...input,context,reports})).decision,'supported_attempt');
    console.log(JSON.stringify({ok:true,pluginVersion,name:skill.name,description:skill.description,
      loadedBytes:Buffer.byteLength(loaded.content),resourceBase:loaded.resourceBase,
      assessmentSkill:assessment.name,assessmentTool:true,assessmentRpcCases:3,
      multiArmySkill:multi.name,huntTools:4,huntStatusRpc:true,huntExecutionEnabled:false,
      replenishTool:true,replenishUnavailableGuard:true,gameActions:0}));
  } finally { await ctx.fiber.dispose(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
