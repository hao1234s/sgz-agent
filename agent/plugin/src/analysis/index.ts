import type {Context} from '../core/types.js';
import type {ResolvedConfig} from '../core/config.js';
import type {WorkerClient} from '../core/worker.js';
import {registerTools} from '../core/register.js';
import {definitions} from './tools.js';
import {createWorker} from './worker.js';
import {prompt} from './prompt.js';
export function register(ctx:Context,config:ResolvedConfig):void {
  let worker:WorkerClient|undefined;
  ctx.effect(()=>async()=>{await worker?.close();},'sgz analysis worker');
  ctx.systemPrompt.section({name:'sgz:analysis',order:500,text:prompt});
  registerTools(ctx,definitions,(method,args)=>(worker??=createWorker(config)).call(method,args),()=>true);
}
