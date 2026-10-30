import {resolve} from 'node:path';
export interface Capabilities {analysis: boolean; client: boolean; knowledge: boolean;}
interface PythonOptions {pythonPath?: string;}
export interface Config {
  rootDir: string; pythonPath?: string; configPath?: string; autoStart?: boolean;
  capabilities?: Partial<Capabilities>;
  analysis?: PythonOptions;
  client?: PythonOptions & {configPath?: string; autoStart?: boolean};
  knowledge?: PythonOptions & {directory?: string};
}
export interface ResolvedConfig {
  rootDir: string; capabilities: Capabilities; explicitCapabilities: boolean;
  analysis: {pythonPath: string};
  client: {pythonPath: string; configPath?: string; autoStart: boolean};
  knowledge: {pythonPath: string; directory: string};
}
export function resolveConfig(input: Config, platform: NodeJS.Platform = process.platform): ResolvedConfig {
  if (!input.rootDir) throw new Error('sgz-agent rootDir is required');
  const rootDir = resolve(input.rootDir);
  const explicitCapabilities = input.capabilities !== undefined;
  const capabilities: Capabilities = {analysis: !explicitCapabilities, client: !explicitCapabilities, knowledge: !explicitCapabilities};
  if (explicitCapabilities) {
    if (!input.capabilities || typeof input.capabilities !== 'object' || Array.isArray(input.capabilities))
      throw new Error('invalid_capabilities');
    for (const [key, value] of Object.entries(input.capabilities)) {
      if (!Object.hasOwn(capabilities, key) || typeof value !== 'boolean') throw new Error('invalid_capability:' + key);
      capabilities[key as keyof Capabilities] = value;
    }
  }
  const python = (options?: PythonOptions) => options?.pythonPath ?? input.pythonPath ??
    resolve(rootDir, platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  return {rootDir, capabilities, explicitCapabilities,
    analysis: {pythonPath: python(capabilities.analysis ? input.analysis : undefined)},
    client: {pythonPath: python(capabilities.client ? input.client : undefined),
      configPath: capabilities.client ? input.client?.configPath ?? input.configPath : undefined,
      autoStart: capabilities.client && (input.client?.autoStart ?? input.autoStart ?? true)},
    knowledge: {pythonPath: python(capabilities.knowledge ? input.knowledge : undefined),
      directory: capabilities.knowledge && input.knowledge?.directory ? resolve(rootDir,input.knowledge.directory) : resolve(rootDir,'.state/knowledge')},
  };
}
export function skillDirectories(config: ResolvedConfig): string[] {
  return [config.capabilities.analysis && resolve(config.rootDir,'agent/skills/conversation'),
    config.capabilities.client && resolve(config.rootDir,'agent/skills/client')].filter((x): x is string => !!x);
}
