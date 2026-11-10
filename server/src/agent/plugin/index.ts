import {resolveConfig, skillDirectories, type Config} from './core/config.js';
import type {Context} from './core/types.js';
import {registerConversation} from './conversation/index.js';
import {configureNativeSkills} from './conversation/provider.js';
export const name = 'sgz-agent';
export const inject = ['tools','skills','systemPrompt'];
export async function apply(ctx: Context, input: Config): Promise<void> {
  const config = resolveConfig(input);
  if (config.explicitCapabilities) await configureNativeSkills(ctx,skillDirectories(config));
  registerConversation(ctx,{rootDir:config.rootDir,directories:skillDirectories(config),legacy:!config.explicitCapabilities});
  if (config.capabilities.analysis) (await import('./analysis/index.js')).register(ctx,config);
  if (config.capabilities.knowledge) (await import('./knowledge/index.js')).register(ctx,config);
  if (config.capabilities.client) (await import('./client/index.js')).register(ctx,config);
}
