import {isAbsolute, relative, resolve, sep} from 'node:path';
import type {Context, Execution, Skill} from '../core/types.js';
export interface SkillOptions {rootDir:string; directories:string[]; legacy:boolean;}
export function createSkillExecutor(ctx:Context, options:SkillOptions) {
  const eligible=(skill:Skill) => {
    if (skill.provider!=='sgz-filesystem' || !skill.invocation.modelInvocable) return false;
    if (options.legacy) return true; // Preserve custom native roots in old, fully enabled installations.
    const path=skill.resourceBase?.path;
    if (!path) return false;
    return options.directories.some(root=>{const child=relative(root,resolve(path));
      return child==='' || (!isAbsolute(child) && child!=='..' && !child.startsWith('..'+sep));});
  };
  return async (method:string,args:Record<string,unknown>,exec?:Execution) => {
    const lookup={cwd:options.rootDir,scope:exec?.agent,signal:exec?.signal};
    if(method==='skill_load'&&(typeof args.name!=='string'||!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(args.name)))
      throw new Error('invalid_skill_name');
    const snapshot=await ctx.skills.snapshot(lookup);
    const skills=snapshot.skills.filter(eligible);
    if(method==='skills_list')return {directory:resolve(options.rootDir,'agent/skills'),directories:options.directories,
      complete:snapshot.complete,skills:skills.map(({name,description,provider,resourceBase})=>({name,description,provider,resourceBase}))};
    if(!skills.some(s=>s.name===args.name))throw new Error('skill_not_available');
    const skill=await ctx.skills.get(args.name as string,lookup);
    if(!skill||!eligible(skill))throw new Error('skill_not_available');
    return {name:skill.name,provider:skill.provider,resourceBase:skill.resourceBase,content:skill.content};
  };
}
