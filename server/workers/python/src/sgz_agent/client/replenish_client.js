// Pure send-time contract; also executed by offline tests.
function ownCityIdleStatus(raw) {
  const point=p=>Array.isArray(p)&&p.length===2&&p.every(v=>Number.isInteger(v)&&v>=1&&v<=1620);
  const has=k=>Object.prototype.hasOwnProperty.call(raw,k);
  const clear=v=>v===null||v===0;
  return !!raw&&typeof raw==='object'&&!Array.isArray(raw)&&
    Number.isSafeInteger(raw.army_id)&&raw.army_id>0&&raw.army_type===1&&
    Number.isSafeInteger(raw.city_id)&&raw.city_id>0&&raw.dispatch_build_id===raw.city_id&&
    Array.isArray(raw.status_codes)&&raw.status_codes.length===1&&raw.status_codes[0]===1&&
    raw.army_status===1&&raw.system_status===1&&
    raw.movement_end_time===0&&raw.movement_total_time===0&&raw.at_dispatch===true&&
    point(raw.position)&&point(raw.dispatch_position)&&
    raw.position[0]===raw.dispatch_position[0]&&raw.position[1]===raw.dispatch_position[1]&&
    ['command','destination','enemy_army_id','fight_position'].every(has)&&
    clear(raw.command)&&raw.destination===null&&clear(raw.enemy_army_id)&&raw.fight_position===null;
}
function ordinaryReserveVerified(status) {
  return status.is_person_guard===false&&status.person_guard_index===0;
}
function replenishAckDecision(state) {
  // Reentrant native calls can suppress our own encoder hook. The explicit
  // single invocation, both server acknowledgements and full state are required.
  if(state.native_calls!==1||state.ambiguous||state.encoded>1)return 'unknown';
  if(state.army_ack===false||state.response_ack===false)return 'rejected';
  return state.army_ack===true&&state.response_ack===true?'verify':'wait';
}
function validateReplenish(plan,row) {
  if(!row||row.phase!=='idle')return 'army_not_idle';
  if(!row.home_city_id||row.current_base_id!==row.home_city_id)return 'not_in_own_city';
  if(row.ordinary_verified!==true)return 'ordinary_reserve_unverified';
  if(row.recruitment_clear!==true)return 'recruitment_state_unverified';
  const q=plan&&plan.request,heroes=row.heroes,expected=plan&&plan.heroes;
  if(!q||plan.army_id!==row.army_id||plan.home_city_id!==row.home_city_id||q.cons_index!==1||
     q.army_id!==Number(row.army_id)||!Array.isArray(heroes)||!Array.isArray(expected)||
     !Array.isArray(q.hero_id_list)||!Array.isArray(q.hero_troop_list)||
     heroes.length<1||heroes.length>3||expected.length!==heroes.length||
     q.hero_id_list.length!==heroes.length||q.hero_troop_list.length!==heroes.length)
    return 'invalid_replenish_request';
  for(let i=0;i<heroes.length;i++) {
    const h=heroes[i],e=expected[i];
    if(!h||!e||h.id!==e.id||!Number.isSafeInteger(Number(h.id))||Number(h.id)<=0||
       !Number.isInteger(h.capacity)||h.capacity<=0||h.capacity>100000||h.capacity!==e.capacity||
       !Number.isInteger(h.troops)||h.troops<0||h.troops>h.capacity||
       q.hero_id_list[i]!==Number(h.id)||q.hero_troop_list[i]!==h.capacity)
      return 'invalid_replenish_request';
    if(h.recovering===true)return 'wounded_recovery_pending';
  }
  if(new Set(heroes.map(h=>h.id)).size!==heroes.length)return 'invalid_replenish_request';
  if(heroes.every(h=>h.troops===h.capacity))return 'already_full';
  if(heroes.some((h,i)=>h.troops!==expected[i].troops))return 'troop_snapshot_changed';
  const deficit=heroes.reduce((sum,h)=>sum+h.capacity-h.troops,0);
  if(!Number.isInteger(row.reserve)||row.reserve<deficit)return 'reserve_insufficient';
  return null;
}

// Native adapter: loaded after the existing hunt read helpers, without their hook.
function tableEmpty(L,index) {
  if(type(L,index)!==5)return false;
  const saved=top(L);pushnil(L);const empty=next(L,index)===0;settop(L,saved);return empty;
}
function refillSnapshot(L,armyId) {
  const initial=top(L);
  try {
    const mgr=module(L,'logic.player.army.my_army_mgr');get(L,mgr,'army_id_tbl');
    if(type(L,-1)!==5||rawi(L,top(L),armyId)!==5)throw Error('replenish_army_not_owned');
    const army=top(L),raw=statusOnly(L,army);
    const row={army_id:String(armyId),home_city_id:String(raw.city_id),phase:'unavailable',
      current_base_id:null,ordinary_verified:false,recruitment_clear:false,
      status_codes:raw.status_codes,is_tying:raw.is_tying,at_dispatch:raw.at_dispatch};
    if(!ownCityIdleStatus(raw))return row;
    row.phase='idle';
    if(raw.dispatch_build_id!==raw.city_id||raw.at_dispatch!==true)return row;
    const player=module(L,'logic.player.player_mgr');get(L,player,'city');get(L,-1,'cities');
    if(type(L,-1)!==5)throw Error('replenish_cities_missing');
    const cities=top(L);let ownCity=false,n=0;pushnil(L);
    while(next(L,cities)) {
      if(++n>20)throw Error('replenish_city_limit');
      const city=top(L);
      if(value(L,city,'city_id')===raw.city_id&&[1,2].includes(value(L,city,'city_type')))ownCity=true;
      settop(L,city-1);
    }
    if(!ownCity)return row;
    row.current_base_id=row.home_city_id;
    get(L,army,'status');const reserveStatus=top(L);
    row.is_person_guard=val(L,reserveStatus,'is_person_guard');
    row.person_guard_index=value(L,reserveStatus,'person_guard_index');
    row.ordinary_verified=ordinaryReserveVerified(row);
    if(!row.ordinary_verified)return row;
    OPTIONS.army_id=armyId;
    const ready=readyArmy(L);row.heroes=ready.heroes;
    for(const hero of row.heroes) {
      hero.recovering=typeof hero.wounded_until!=='number'||hero.wounded_until>Date.now()/1000;
    }
    const cm=module(L,'logic.conscription.conscription_mgr');
    get(L,cm,'army_conscription_infos');const clearA=tableEmpty(L,top(L));settop(L,cm);
    get(L,cm,'conscription_info_dict');const clearB=tableEmpty(L,top(L));settop(L,cm);
    row.recruitment_clear=clearA&&clearB;
    get(L,cm,'barrack_infos');
    if(type(L,-1)!==5||rawi(L,top(L),raw.city_id)!==5)return row;
    get(L,-1,'tb');if(type(L,-1)!==5||rawi(L,top(L),1)!==5)return row;
    row.reserve=value(L,top(L),'cur_troops');
    return row;
  } finally {settop(L,initial);}
}
function refillRoster(L) {
  const initial=top(L),all=roster(L),selected=OPTIONS.army_ids,armies=[];
  settop(L,initial);
  for(const raw of all.armies) {
    if(raw.army_type!==1||!raw.hero_ids.some(id=>Number.isSafeInteger(id)&&id>0))continue;
    if(selected&&!selected.includes(String(raw.army_id)))continue;
    if(armies.length>=50)throw Error('replenish_roster_limit');
    armies.push(refillSnapshot(L,raw.army_id));
  }
  return {complete:all.complete,armies,observed_at:Date.now()/1000};
}
let busy=false,finished=false,attempted=false,started=false,ownCall=false;
let armyAck=null,responseAck=null,encoded=0,ambiguous=false,deadlineTimer=null;
let nativeCalls=0,sendingThread=null;
function finishRefill(data) {
  if(finished)return;finished=true;
  if(deadlineTimer!==null)clearTimeout(deadlineTimer);
  const diagnostic={native_calls:nativeCalls,encoded,ambiguous,army_ack:armyAck,response_ack:responseAck};
  lifecycle.finish({kind:'complete',data:OPTIONS.operation==='execute'?{...diagnostic,...data}:data});
}
function failRefill(error) {
  if(finished)return;finished=true;
  if(deadlineTimer!==null)clearTimeout(deadlineTimer);
  lifecycle.finish({kind:'failure',reason:String(error),attempted});
}
function installAndSend(L,plan) {
  get(L,-1001000,'_LOADED');const loaded=top(L);get(L,loaded,'network.network');const factory=top(L);
  let found=false;
  for(let n=1;n<=40;n++) {const p=upvalue(L,factory,n);if(p.isNull())break;if(p.readUtf8String()==='Global'){found=true;break;}settop(L,factory);}
  if(!found)throw Error('replenish_network_unavailable');
  get(L,-1,'network');const network=top(L);get(L,network,'rpc');const rp=top(L);
  get(L,rp,'v_server_proto');get(L,-1,'__cobj');const server=top(L);
  get(L,rp,'v_client_proto');get(L,-1,'__cobj');const receiver=top(L);
  const core=module(L,'sproto.core');
  get(L,core,'protocol');pushvalue(L,server);pushstring(L,key('cs_army_set_troops'));
  if(call(L,2,3,0,0,ptr(0))!==0||scalar(L,-3)!==902||type(L,-2)!==2||type(L,-1)!==2)throw Error('replenish_schema_mismatch');
  const requestType=pointer(L,-2).toString(),responseType=pointer(L,-1).toString();settop(L,core);
  get(L,core,'protocol');pushvalue(L,receiver);pushstring(L,key('sc_army_set_troops'));
  if(call(L,2,3,0,0,ptr(0))!==0||scalar(L,-3)!==1091||type(L,-2)!==2)throw Error('replenish_notification_mismatch');
  const notifyType=pointer(L,-2).toString();settop(L,core);
  const addresses=new Set();
  for(const name of ['encode','encodep','decode','decodep','decoden']) {
    get(L,core,name);const address=cfunction(L,-1);settop(L,core);
    if(address.isNull())throw Error('replenish_codec_unavailable');
    if(addresses.has(address.toString()))continue;addresses.add(address.toString());
    const encoding=name.startsWith('encode');
    lifecycle.attach(address,{onEnter(args){
      this.state=args[0];this.kind=null;if(finished)return;
      const token=pointer(this.state,1).toString();
      if(encoding&&token===requestType) {if(!ownCall||this.threadId!==sendingThread)ambiguous=true;else encoded++;}
      if(!encoding)this.kind=token===responseType?'response':token===notifyType?'army':null;
    },onLeave(ret){
      if(encoding||!this.kind||finished)return;const state=this.state,saved=top(state);
      try {
        const i=decodedReturnIndex(saved,ret.toInt32());
        if(!checkstack(state,32)||type(state,i)!==5)throw Error('replenish_ack_invalid');
        if(this.kind==='army') {if(value(state,i,'army_id')===plan.request.army_id)armyAck=val(state,i,'result');}
        else responseAck=val(state,i,'result');
      } catch(error){failRefill(error);}
      finally{settop(state,saved);}
    }});
  }
  const create=fn('lua_createtable','void',['pointer','int','int']);
  const set=fn('lua_setfield','void',['pointer','int','pointer']);
  const seti=fn('lua_rawseti','void',['pointer','int','int64']);
  get(L,network,'call');pushvalue(L,network);pushstring(L,key('cs_army_set_troops'));create(L,0,4);const args=top(L);
  for(const k of ['army_id','cons_index']){pushinteger(L,plan.request[k]);set(L,args,key(k));}
  for(const k of ['hero_id_list','hero_troop_list']) {
    create(L,plan.request[k].length,0);const array=top(L);
    plan.request[k].forEach((v,i)=>{pushinteger(L,v);seti(L,array,i+1);});set(L,args,key(k));
  }
  if(attempted||nativeCalls!==0)throw Error('replenish_already_attempted');
  Interceptor.flush();
  attempted=true;nativeCalls++;sendingThread=Process.getCurrentThreadId();ownCall=true;
  try {if(call(L,3,0,0,0,ptr(0))!==0)throw Error('replenish_call_failed');}
  finally {ownCall=false;}
  started=true;
  deadlineTimer=setTimeout(()=>finishRefill({status:'unknown',sent:true,reason:'ack_or_full_troops_timeout',army_ack:armyAck,response_ack:responseAck}),12000);
}
lifecycle.attach(client.getExportByName('lua_pcallk'),{onEnter(args){
  if(busy||finished)return;busy=true;const L=args[0],saved=top(L);
  try {
    if(!checkstack(L,256))throw Error('replenish_stack_capacity');identity(L);
    if(OPTIONS.operation==='snapshot') {const data=refillRoster(L);settop(L,saved);identity(L);finishRefill(data);return;}
    if(OPTIONS.operation!=='execute')throw Error('replenish_operation_not_allowed');
    const plan=OPTIONS.plan;
    if(!started) {
      const row=refillSnapshot(L,Number(plan.army_id)),reason=validateReplenish(plan,row);
      if(reason){finishRefill({status:reason==='already_full'?'already_full':'blocked',sent:false,reason});return;}
      settop(L,saved);identity(L);installAndSend(L,plan);return;
    }
    const decision=replenishAckDecision({native_calls:nativeCalls,encoded,ambiguous,army_ack:armyAck,response_ack:responseAck});
    if(decision==='unknown'){finishRefill({status:'unknown',sent:true,reason:'replenish_correlation_ambiguous'});return;}
    if(decision==='rejected'){finishRefill({status:'rejected',sent:true,reason:'server_rejected'});return;}
    if(decision==='wait')return;
    const row=refillSnapshot(L,Number(plan.army_id));
    // Snapshot native calls yield the JS lock; recheck traffic/acks afterwards.
    const after=replenishAckDecision({native_calls:nativeCalls,encoded,ambiguous,army_ack:armyAck,response_ack:responseAck});
    if(after==='unknown'){finishRefill({status:'unknown',sent:true,reason:'replenish_correlation_ambiguous'});return;}
    if(after==='rejected'){finishRefill({status:'rejected',sent:true,reason:'server_rejected'});return;}
    if(after==='wait')return;
    if(row.phase!=='idle'){finishRefill({status:'unknown',sent:true,reason:'phase_changed_after_send'});return;}
    if(validateReplenish(plan,row)==='already_full')
      finishRefill({status:'applied',sent:true,army_id:row.army_id,heroes:row.heroes,reserve_after:row.reserve,army_ack:true,response_ack:true});
  } catch(error){failRefill(error);}
  finally{settop(L,saved);busy=false;}
}});
