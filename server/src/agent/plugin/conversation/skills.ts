import {isAbsolute, relative, resolve, sep} from 'node:path';
import type {Context, Execution, Skill} from '../core/types.js';
import {readFile, realpath, stat} from 'node:fs/promises';

/** Load only markdown linked by the trusted skill, never a caller-supplied path. */
async function loadReferences(skill:Skill):Promise<Record<string,string>> {
  const links=[...new Set([...String(skill.content??'').matchAll(/\]\((references\/[^\s)]+\.md)\)/g)].map(match=>match[1]))];
  if(!links.length)return {};
  if(!skill.resourceBase?.path)throw new Error('skill_resource_unavailable');
  if(links.length>16)throw new Error('skill_references_too_large');
  const base=await realpath(skill.resourceBase.path), references:Record<string,string>={};
  let bytes=0;
  for(const link of links) {
    const target=await realpath(resolve(base,link)), child=relative(base,target);
    if(isAbsolute(child)||child==='..'||child.startsWith('..'+sep))throw new Error('skill_reference_outside_root');
    const info=await stat(target);bytes+=info.size;
    if(!info.isFile()||bytes>128*1024)throw new Error('skill_references_too_large');
    references[link]=await readFile(target,'utf8');
  }
  return references;
}
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
    if(method==='skills_list')return {directory:resolve(options.rootDir,'server/skills'),directories:options.directories,
      complete:snapshot.complete,skills:skills.map(({name,description,provider,resourceBase})=>({name,description,provider,resourceBase}))};
    if(!skills.some(s=>s.name===args.name))throw new Error('skill_not_available');
    const skill=await ctx.skills.get(args.name as string,lookup);
    if(!skill||!eligible(skill))throw new Error('skill_not_available');
    return {name:skill.name,provider:skill.provider,resourceBase:skill.resourceBase,content:skill.content,references:await loadReferences(skill)};
  };
}
