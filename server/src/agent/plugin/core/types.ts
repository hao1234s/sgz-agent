export type Schema = Record<string, unknown>;
export interface Skill {
  name: string; description?: string; provider: string;
  invocation: {modelInvocable: boolean}; content?: string;
  resourceBase?: {kind?: string; path?: string};
}
export interface Lookup {cwd: string; scope?: unknown; signal?: AbortSignal;}
export interface Execution {agent?: unknown; signal?: AbortSignal;}
export interface Context {
  get?: (name:string) => unknown;
  tools: {register: (tool: any) => unknown};
  skills: {snapshot: (lookup: Lookup) => Promise<{skills: Skill[]; complete: boolean}>;
    get: (name: string, lookup: Lookup) => Promise<Skill | undefined>};
  systemPrompt: {section: (section: {name: string; order: number; text: string}) => unknown};
  effect: (factory: () => () => Promise<void>, label?: string) => unknown;
}
export interface ToolDefinitions {
  descriptions: Record<string, string>;
  parameters: Record<string, {properties: Record<string, Schema>; required: string[]}>;
}
