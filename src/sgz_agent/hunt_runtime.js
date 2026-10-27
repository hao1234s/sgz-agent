// Persistent fixed-operation queue. Every Lua read runs at a fresh native entry;
// no Lua state pointer survives its callback or enters a timer callback.
let runtimeClosing=false, runtimeBusy=false, runtimeInstalled=false;
let runtimeJob=null, runtimeOutbox=null, runtimePumpScheduled=false;
let runtimeHeartbeat=null, runtimeLastNative=0;
const runtimeObserver=createHuntObserver();
function runtimePublishEvent(event) {
  if(!runtimeClosing)send({...event,generation:OPTIONS.generation});
}
function runtimeClearTimers() {
  if(runtimeHeartbeat!==null){clearInterval(runtimeHeartbeat);runtimeHeartbeat=null;}
}
function runtimeFail(error) {
  if(runtimeClosing)return;
  runtimeClosing=true;runtimeJob=null;runtimeOutbox=null;
  runtimeClearTimers();runtimeObserver.close();
  lifecycle.finish({kind:'runtime_failure',generation:OPTIONS.generation,reason:String(error.message||error)});
}
function runtimeSchedule() {
  if(runtimePumpScheduled||runtimeClosing)return;
  runtimePumpScheduled=true;
  setImmediate(()=>{
    runtimePumpScheduled=false;
    if(runtimeClosing||runtimeBusy)return;
    const item=runtimeOutbox;runtimeOutbox=null;
    if(item)runtimePublishEvent(item);
  });
}
function runtimeArguments(operation,args) {
  if(!args||typeof args!=='object'||Array.isArray(args))throw Error('hunt_runtime_arguments_invalid');
  const keys=Object.keys(args).sort().join(',');
  if(operation==='attack'||operation==='attack_inputs') {
    if(!OPTIONS.controlled_attack_plan||keys!=='')throw Error('controlled_attack_not_enabled');
  } else if(operation==='roster'||operation==='bases') {
    if(keys!=='')throw Error('hunt_runtime_arguments_invalid');
  } else if(operation==='ready') {
    if(keys!=='army_id'||!Number.isSafeInteger(args.army_id)||args.army_id<=0)
      throw Error('hunt_runtime_army_id_invalid');
  } else if(operation==='cache_search') {
    if(keys!=='center,radius'||!Array.isArray(args.center)||args.center.length!==2||
      !args.center.every(n=>Number.isInteger(n)&&n>=1&&n<=1620)||
      !Number.isInteger(args.radius)||args.radius<1||args.radius>70)
      throw Error('hunt_runtime_search_bounds_invalid');
  } else throw Error('hunt_runtime_operation_not_allowed');
}
function runtimeRead(L,job) {
  if(job.operation==='attack_inputs')return runtimeAttackInputs(L);
  if(job.operation==='attack')return runtimeAttack(L);
  if(job.operation==='roster')return roster(L);
  if(job.operation==='ready') {
    const saved=top(L);
    OPTIONS.army_id=job.args.army_id;
    try {
      // readyArmy repeats the idle guard before its existing detail reads.
      const data=readyArmy(L);settop(L,saved);
      const mgr=module(L,'logic.player.army.my_army_mgr');get(L,mgr,'army_id_tbl');
      if(type(L,-1)!==5||rawi(L,top(L),job.args.army_id)!==5)throw Error('hunt_army_not_owned');
      const army=top(L);
      if(!isReady(statusOnly(L,army)))throw Error('army_not_ready_for_details');
      get(L,army,'status');
      if(type(L,-1)!==5)throw Error('hunt_status_missing');
      const status=top(L),parameters={source:'own_army.status'};
      for(const name of ['cur_shiqi_cost_percell','cur_shiqi_max','cur_shiqi_reco_speed','shiqi_begin_count_time'])
        parameters[name]=value(L,status,name);
      return {...data,morale_parameters:parameters};
    }finally{settop(L,saved);delete OPTIONS.army_id;}
  }
  if(job.operation==='bases')return bases(L);
  if(job.operation==='cache_search') {
    const data=cachedGiants(L),center=job.args.center,radius=job.args.radius;
    data.targets=data.targets.filter(t=>(t.x-center[0])**2+(t.y-center[1])**2<=radius**2);
    return {...data,center,radius};
  }
  throw Error('hunt_runtime_operation_not_allowed');
}
lifecycle.attach(client.getExportByName('lua_pcallk'),{onEnter(args) {
  this.runtimeTracked=false;
  if(runtimeClosing||runtimeBusy)return;
  runtimeLastNative=Date.now()/1000;
  if(runtimeInstalled&&!runtimeJob)return;
  runtimeBusy=true;this.runtimeTracked=true;
  const L=args[0],saved=top(L);
  try {
    if(!checkstack(L,256))throw Error('hunt_runtime_stack_capacity');
    identity(L);
    if(!runtimeInstalled) {
      runtimeObserver.install(L);runtimeInstalled=true;
      runtimeOutbox={kind:'runtime_ready',epoch:Date.now()/1000};
      runtimeHeartbeat=setInterval(()=>runtimePublishEvent({kind:'runtime_heartbeat',
        epoch:Date.now()/1000,last_native_at:runtimeLastNative,observer:runtimeObserver.state()}),15000);
    } else {
      const job=runtimeJob;runtimeJob=null;
      const data=runtimeRead(L,job);
      runtimeOutbox={kind:'request_result',id:job.id,operation:job.operation,data};
    }
    settop(L,saved);identity(L);
  }catch(error){runtimeFail(error);}
  finally{settop(L,saved);}
},onLeave() {
  if(this.runtimeTracked!==true)return;
  runtimeBusy=false;runtimeSchedule();
}});
rpc.exports={
  enqueue(job) {
    if(!job||typeof job.id!=='string'||!/^[A-Za-z0-9_-]{1,64}$/.test(job.id)||
      Object.keys(job).sort().join(',')!=='args,id,operation')throw Error('hunt_runtime_job_invalid');
    runtimeArguments(job.operation,job.args);
    if(runtimeClosing)throw Error('hunt_runtime_closing');
    if(!runtimeInstalled)throw Error('hunt_runtime_not_ready');
    if(runtimeJob||runtimeBusy||runtimeOutbox)throw Error('hunt_runtime_queue_busy');
    runtimeJob=JSON.parse(JSON.stringify(job));
    return {queued:job.id};
  },
  close() {
    runtimeClosing=true;runtimeJob=null;runtimeOutbox=null;
    runtimeClearTimers();runtimeObserver.close();return lifecycle.close();
  },
  state() {
    return {...lifecycle.state(),generation:OPTIONS.generation,
      installed:runtimeInstalled,pending:runtimeJob?runtimeJob.id:null,
      last_native_at:runtimeLastNative,observer:runtimeObserver.state()};
  }
};
