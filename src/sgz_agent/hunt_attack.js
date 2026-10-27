// Pure, strict send-time guard. Executed in Node regression tests and native code.
function validateAttack(plan,row,target,now) {
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const point=p=>Array.isArray(p)&&p.length===2&&p.every(v=>Number.isInteger(v)&&v>=1&&v<=1620);
  const same=(a,b)=>point(a)&&point(b)&&a[0]===b[0]&&a[1]===b[1];
  if(!plan||!row||!target||!finite(now)||!finite(plan.created_at)||!finite(plan.expires_at)||
      plan.created_at>now||plan.expires_at<=now||plan.expires_at-plan.created_at>30)
    return 'attack_plan_stale';
  const a=row.army,t=plan.target;
  if(!a||String(a.army_id)!==plan.army_id||a.army_type!==1||a.is_tying!==false||
      !Array.isArray(a.status_codes)||a.status_codes.length!==1||![1,3,19,43].includes(a.status_codes[0]))
    return 'army_not_ready';
  if(!same(a.position,plan.position)||!same(a.position,a.dispatch_position)||
      String(a.dispatch_build_id)!==plan.base_id||!finite(a.dispatch_entered_at)||
      a.dispatch_entered_at<=0||a.dispatch_entered_at>now||
      a.movement_end_time!==0||a.movement_total_time!==0)return 'army_base_changed';
  if(!t||String(target.id)!==t.id||target.use_code!==1||target.conf_id!==10||
      target.x!==t.x||target.y!==t.y||!point([target.x,target.y])||
      !finite(target.end_time)||target.end_time<=now||!finite(t.observed_at)||
      t.observed_at>now||now-t.observed_at>30)return 'target_changed_or_stale';
  if(plan.defenders_verified!==true||row.defender_count!==3||row.cost_decree!==1||
      !Number.isInteger(row.orders_available)||row.orders_available<1||
      !Number.isInteger(plan.orders_available)||plan.orders_available<1)
    return 'target_or_cost_unverified';
  const hs=row.heroes,expected=plan.heroes;
  if(!Array.isArray(hs)||hs.length!==3||!Array.isArray(expected)||expected.length!==3||
      new Set(hs.map(h=>h.id)).size!==3)return 'heroes_invalid';
  let total=0;
  for(let i=0;i<3;i++) {
    const h=hs[i],e=expected[i];
    if(!h||!e||h.id!==e.id||!Number.isInteger(h.troops)||h.troops!==e.troops||
        !Number.isInteger(h.capacity)||h.capacity!==e.capacity||h.troops<=0||h.troops>h.capacity||
        !finite(h.stamina)||h.stamina<15||!finite(h.wounded_until)||h.wounded_until>now)
      return 'heroes_changed_or_unready';
    total+=h.troops;
  }
  if(plan.min_troops!==20000||total<20000||plan.stamina_cost!==15||!finite(row.stamina)||row.stamina<15)
    return 'troops_or_stamina_insufficient';
  const p=plan.preview;
  if(!p||p.result!==1||!finite(p.path_len)||p.path_len<0||!finite(p.need_time)||p.need_time<0||
      p.need_energy!==15||!finite(plan.morale_cost_per_cell)||plan.morale_cost_per_cell<0||
      row.morale_cost_per_cell!==plan.morale_cost_per_cell||
      !finite(plan.min_morale)||plan.min_morale<0||!finite(plan.arrival_morale)||
      plan.arrival_morale<plan.min_morale||!finite(row.morale)||
      Math.max(0,row.morale-p.path_len*row.morale_cost_per_cell)<plan.min_morale)
    return 'arrival_morale_insufficient_or_unverified';
  return null;
}

function createAttackExecutor(plan,api) {
  let attempted=false,finished=false;
  return {run(now) {
    if(attempted||finished)return {status:'unknown',sent:attempted,reason:'attack_already_attempted'};
    try {
      const reason=validateAttack(plan,api.snapshot(),api.target(),now);
      if(reason){finished=true;return {status:'rejected',sent:false,reason};}
      attempted=true;
      api.send();
      finished=true;
      return {status:'submitted',sent:true};
    }catch(error){
      finished=true;
      return {status:attempted?'unknown':'rejected',sent:attempted,reason:String(error.message||error)};
    }
  }};
}

// Native transport: one hard-coded TAOFA request; no arbitrary RPC input.
function runtimeAttackInputs(L) {
  const plan=OPTIONS.controlled_attack_plan,saved=top(L);
  if(!plan)throw Error('controlled_attack_not_enabled');
  try {
    OPTIONS.army_id=Number(plan.army_id);
    const row=readyArmy(L);settop(L,saved);
    const player=module(L,'logic.player.player_mgr');
    row.orders_available=scalar(L,invoke(L,player,'get_resource_val',[7]));settop(L,saved);
    const mgr=module(L,'logic.player.army.my_army_mgr');get(L,mgr,'army_id_tbl');
    if(rawi(L,top(L),Number(plan.army_id))!==5)throw Error('hunt_army_not_owned');
    const army=top(L);if(!isReady(statusOnly(L,army)))throw Error('army_not_ready_for_details');
    get(L,army,'status');const status=top(L);
    row.morale_cost_per_cell=value(L,status,'cur_shiqi_cost_percell');settop(L,saved);
    const base=module(L,'logic.map.npc.aoi_npcbase');get(L,base,'get_name');const factory=top(L);let found=false;
    for(let n=1;n<=40;n++) {
      const p=upvalue(L,factory,n);if(p.isNull())break;
      if(p.readUtf8String()==='Global'){found=true;break;}settop(L,factory);
    }
    if(!found)throw Error('attack_config_global_missing');
    get(L,-1,'share_res');get(L,-1,'cfg_data_dict');const configs=top(L);
    const geti=fn('lua_geti','int',['pointer','int','int64']);
    get(L,configs,'resource');geti(L,top(L),7);const resource=top(L);
    if(value(L,resource,'id')!==7||value(L,resource,'en_name')!=='decree')throw Error('attack_resource_changed');
    settop(L,configs);get(L,configs,'monster_npc');geti(L,top(L),10);const monster=top(L);
    row.defender_count=value(L,monster,'army_num');row.target_level=value(L,monster,'level');
    settop(L,configs);get(L,configs,'army_cmd');geti(L,top(L),12);const cmd=top(L);
    row.cost_decree=value(L,cmd,'cost_decree');row.cost_energy=value(L,cmd,'energy');
    row.can_auto_return=val(L,cmd,'can_auto_fallback');
    if(row.target_level!==10||row.cost_energy!==15||row.can_auto_return!==true)
      throw Error('attack_command_config_changed');
    return row;
  }finally{settop(L,saved);delete OPTIONS.army_id;}
}
let controlledAttackInvoked=false;
function runtimeAttack(L) {
  const plan=OPTIONS.controlled_attack_plan;
  if(!plan)throw Error('controlled_attack_not_enabled');
  if(controlledAttackInvoked)return {status:'unknown',sent:true,reason:'attack_already_attempted'};
  controlledAttackInvoked=true;
  // Keep this executor local: its adapters reference L only during this callback.
  const executor=createAttackExecutor(plan,{
    snapshot:()=>runtimeAttackInputs(L),
    // A correlated server AOI refresh is required by the Python preflight.
    // Its timestamp expires rapidly; the server may still reject a disappearing
    // target. Never reinterpret that rejection as departure.
    target:()=>plan.target,
    send() {
      const saved=top(L);
      try {
        identity(L);
        get(L,-1001000,'_LOADED');const loaded=top(L);
        get(L,loaded,'network.network');const factory=top(L);let found=false;
        for(let n=1;n<=40;n++) {
          const p=upvalue(L,factory,n);if(p.isNull())break;
          if(p.readUtf8String()==='Global'){found=true;break;}
          settop(L,factory);
        }
        if(!found)throw Error('attack_network_unavailable');
        get(L,-1,'network');const network=top(L);
        get(L,network,'rpc');get(L,-1,'v_server_proto');get(L,-1,'__cobj');const schema=top(L);
        const core=module(L,'sproto.core');
        get(L,core,'protocol');pushvalue(L,schema);pushstring(L,key('cs_army_cmd'));
        if(call(L,2,3,0,0,ptr(0))!==0||scalar(L,-3)!==501||type(L,-2)!==2||type(L,-1)!==2)
          throw Error('attack_protocol_mismatch');
        settop(L,core);
        const create=fn('lua_createtable','void',['pointer','int','int']);
        const set=fn('lua_setfield','void',['pointer','int','pointer']);
        const pushbool=fn('lua_pushboolean','void',['pointer','int']);
        function integer(i,k,v){pushinteger(L,v);set(L,i,key(k));}
        function bool(i,k,v){pushbool(L,v?1:0);set(L,i,key(k));}
        get(L,network,'call');pushvalue(L,network);pushstring(L,key('cs_army_cmd'));
        create(L,0,9);const args=top(L);
        integer(args,'cmd',12);integer(args,'army_id',Number(plan.army_id));integer(args,'cmd_target_type',1);
        bool(args,'is_auto_fallback',true);bool(args,'is_occupy',false);
        pushstring(L,key(''));set(L,args,key('ext_str'));
        create(L,0,2);const pos=top(L);integer(pos,'row',plan.target.x);integer(pos,'col',plan.target.y);
        set(L,args,key('target_pos'));
        create(L,0,0);set(L,args,key('path_pos'));
        create(L,0,2);const extra=top(L);bool(extra,'interrupt_guard',true);bool(extra,'cancel_conscription',true);
        set(L,args,key('extra_data'));
        Interceptor.flush();
        if(call(L,3,0,0,0,ptr(0))!==0)throw Error('attack_call_outcome_unknown');
      }finally{settop(L,saved);}
    }
  });
  return executor.run(Date.now()/1000);
}
