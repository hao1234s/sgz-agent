import type {Context} from '../core/types.js';
import type {ResolvedConfig} from '../core/config.js';
import type {WorkerClient} from '../core/worker.js';
import {registerTools} from '../core/register.js';
import {definitions} from './tools.js';
import {createWorker} from './worker.js';
import {prompt} from './prompt.js';
export function register(ctx:Context,config:ResolvedConfig):void {
  let worker:WorkerClient|undefined;
  let startup:Promise<void>=Promise.resolve();
  let startupError:string|undefined;
  const getWorker=()=>worker??=createWorker(config);
  ctx.effect(()=>{
    if(config.client.autoStart) startup=getWorker().call('game_status',{},15_000)
      .then(()=>{}).catch(error=>{startupError=String(error.message??error);});
    return async()=>{await worker?.close();};
  },'sgz client worker');
  ctx.systemPrompt.section({name:'sgz:client',order:510,text:prompt(config.capabilities)});
  registerTools(ctx,definitions,async(method,args)=>{
    await startup;
    if(startupError){
      if(method==='game_status')return {service:{state:'failed',error:startupError}};
      throw new Error(`agent_start_failed:${startupError}`);
    }
    return getWorker().call(method,args,['replenish','hunt_dispatch_start'].includes(method)?3_600_000:120_000);
  },method=>['game_status','protocol_status','hunt_status','hunt_dispatch_status','pause_task','search_map','query_roster'].includes(method));
}
