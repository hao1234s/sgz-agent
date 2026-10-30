import {readFileSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {resolveConfig,skillDirectories} from '../plugin/dist/core/config.js';

export function renderProfile({profile,rootDir,overrides={}}) {
  if(!['chat','knowledge','local'].includes(profile))throw new Error('invalid_profile');
  const template=JSON.parse(readFileSync(new URL(`../profiles/${profile}.json`,import.meta.url),'utf8'));
  const input={...template,...overrides,rootDir:resolve(rootDir)};
  const normalized=resolveConfig(input);
  return [{insert:[
    {id:'sgz-skills',name:'@deepseek-ai/dsh-skill-filesystem',config:{providerName:'sgz-filesystem',
      includeDefaultRoots:false,customSkillDirs:skillDirectories(normalized),watch:true,watchFollowSymlinks:false}},
    {id:'sgz-agent',name:'dsh-sgz-agent',config:input},
  ]}];
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const {values}=parseArgs({options:{profile:{type:'string'},root:{type:'string'},output:{type:'string'},'no-auto-start':{type:'boolean'}}});
  if(!values.profile||!values.root||!values.output)throw new Error('--profile, --root and --output are required');
  // JSON is valid YAML and is accepted by the existing Cordis patch loader.
  writeFileSync(values.output,JSON.stringify(renderProfile({profile:values.profile,rootDir:values.root,overrides:values['no-auto-start']?{client:{autoStart:false}}:{}}),null,2)+'\n');
}
