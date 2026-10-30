import type {Context} from '../core/types.js';
import {registerTools} from '../core/register.js';
import {definitions} from './tools.js';
import {createSkillExecutor, type SkillOptions} from './skills.js';
import {prompt} from './prompt.js';
export function registerConversation(ctx:Context,options:SkillOptions):void {
  ctx.systemPrompt.section({name:'sgz:conversation',order:490,text:prompt});
  registerTools(ctx,definitions,createSkillExecutor(ctx,options),()=>true);
}
