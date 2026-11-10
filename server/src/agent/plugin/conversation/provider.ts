import {resolve} from 'node:path';
import type {Context} from '../core/types.js';

interface ProviderEntry {
  options: {name: string; config?: Record<string,unknown>};
  disabled?: boolean;
  init(): Promise<void>;
  fiber?: {config?: Record<string,unknown>; update(config:unknown,noSave:boolean):void; await():Promise<unknown>};
}

/** Keep the native registry and SGZ wrappers aligned, even with an old provider patch.
 * Cordis Fiber.update(config, true) is runtime-only: it does not write host config.
 * Never update another provider or activate an explicitly disabled entry.
 */
export async function configureNativeSkills(ctx:Context,directories:string[]):Promise<void> {
  const loader=ctx.get?.('loader') as {entries():Iterable<ProviderEntry>}|undefined;
  if(!loader)return; // Standalone integrations without Cordis own their provider registration.
  for(const entry of loader.entries()) {
    if(entry.disabled || entry.options.name!=='@deepseek-ai/dsh-skill-filesystem' ||
       entry.options.config?.providerName!=='sgz-filesystem')continue;
    if(!entry.fiber)await entry.init();
    if(!entry.fiber)throw new Error('sgz_skill_provider_unavailable');
    await entry.fiber.await();
    const previous=entry.fiber.config??entry.options.config;
    const roots=Array.isArray(previous.customSkillDirs)?previous.customSkillDirs.map(String).map(x=>resolve(x)):[];
    if(previous.includeDefaultRoots===false && !previous.bundledSkillDir && previous.watchFollowSymlinks===false &&
       JSON.stringify(roots)===JSON.stringify(directories))continue;
    entry.fiber.update({...previous,includeDefaultRoots:false,bundledSkillDir:undefined,
      customSkillDirs:directories,watchFollowSymlinks:false},true);
    await entry.fiber.await();
  }
}
