import type {Context, Execution, ToolDefinitions} from './types.js';
export function registerTools(ctx: Context, definitions: ToolDefinitions,
  execute: (method: string, args: Record<string,unknown>, exec?: Execution) => Promise<unknown>,
  concurrencySafe: (method: string) => boolean) {
  for (const [method,schema] of Object.entries(definitions.parameters)) ctx.tools.register({
    name: `sgz_${method}`, description: definitions.descriptions[method],
    parameters: {type:'object',...schema,additionalProperties:false},
    output: {schema:{type:'object',additionalProperties:true},
      render: (_args:unknown,value:unknown) => [{type:'text',text:JSON.stringify(value)}]},
    execute: (args:Record<string,unknown>,exec?:Execution) => execute(method,args,exec),
    isConcurrencySafe: () => concurrencySafe(method),
  });
}
