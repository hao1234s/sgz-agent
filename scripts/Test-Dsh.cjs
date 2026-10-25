// Run with the installed DSH Electron binary and ELECTRON_RUN_AS_NODE=1.
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const {pathToFileURL} = require('node:url');

(async () => {
  const root = path.resolve(__dirname, '..');
  const runtime = path.join(path.dirname(process.execPath), 'resources', 'app.asar', 'dsh', 'package.json');
  const r = createRequire(runtime);
  const {boot, composeEntries} = await import(pathToFileURL(r.resolve('@deepseek-ai/dsh-app-boot')).href);
  const yaml = r('js-yaml');
  const patch = yaml.load(fs.readFileSync(path.join(root,'plugin','cordis.patch.yml'),'utf8'));
  const entries = composeEntries([[{insert:[
    {id:'system-prompt',name:'@deepseek-ai/dsh-system-prompt'},
    {id:'tools',name:'@deepseek-ai/dsh-tools'},
  ]}],patch]);
  const agent = entries.find(row=>row.id==='sgz-agent');
  if (!agent) throw Error('bundle_patch_did_not_insert_plugin');
  agent.name = pathToFileURL(path.join(root,'plugin','dist','index.js')).href;
  agent.config.rootDir = root;
  const directory = path.join(root,'.state','dsh-smoke');
  fs.mkdirSync(directory,{recursive:true});
  const configPath = path.join(directory,'cordis.yml');
  fs.writeFileSync(configPath,JSON.stringify(entries,null,2));
  const ctx = await boot('sgz-smoke',configPath,[],undefined,pathToFileURL(runtime).href);
  try {
    const names = ['game_status','prepare_login','observe_map','inspect_target','explore_region','pause_task','resume_task','export_results'];
    for(const name of names)if(!ctx.tools.get(`sgz_${name}`))throw Error(`tool_missing:${name}`);
    const status = await ctx.tools.get('sgz_game_status').execute({});
    if(!Array.isArray(status.windows))throw Error('invalid_worker_status');
    console.log(JSON.stringify({ok:true,registeredTools:names.length,windows:status.windows.length,calibrated:status.calibrated}));
  } finally {
    await ctx.fiber.dispose();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
