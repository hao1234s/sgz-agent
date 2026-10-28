// Pure, strict send-time guard. Executed in Node regression tests and native code.
function attackProfile(plan) {
  const target=plan&&plan.target,conf=target&&target.conf_id;
  if(!target||target.use_code!==1||![9,10].includes(conf))return null;
  const expected={use_code:1,conf_id:conf,display_name:conf===9?'\u91cd\u5f92\u5175':'\u5de8\u621f\u58eb',
    defender_count:conf===9?2:3},supplied=plan.target_profile;
  if(supplied===undefined&&conf===10)return expected;
  if(!supplied||typeof supplied!=='object'||Object.keys(supplied).length!==4||
      Object.entries(expected).some(([key,value])=>supplied[key]!==value))return null;
  return expected;
}

function validateAttackConfig(plan,row) {
  const profile=attackProfile(plan);
  if(!profile||!row||row.defender_count!==profile.defender_count||row.target_level!==profile.conf_id||
      row.cost_decree!==1||row.cost_energy!==15||row.can_auto_return!==true)
    return 'attack_command_config_changed';
  return null;
}

function accountRoute(segments,initialMorale) {
  const finite=v=>typeof v==='number'&&Number.isFinite(v),text=v=>typeof v==='string'&&!!v.trim();
  if(!Array.isArray(segments)||segments.length<1||segments.length>100||!finite(initialMorale)||initialMorale<0)
    throw Error('route_evidence_invalid');
  let seconds=0,cells=0,charged=0,free=0,cost=0;
  for(const segment of segments) {
    if(!segment||typeof segment!=='object'||!['attack','relocate'].includes(segment.kind)||
        !Number.isSafeInteger(segment.actual_path_len)||segment.actual_path_len<0||segment.path_verified!==true||
        !text(segment.path_source)||!finite(segment.seconds)||segment.seconds<0||
        !finite(segment.morale_cost_per_cell)||segment.morale_cost_per_cell<0||
        typeof segment.morale_rule_verified!=='boolean'||!text(segment.morale_rule_source)||
        (segment.morale_cost_per_cell===0&&segment.morale_rule_verified!==true))
      throw Error('route_segment_unverified');
    seconds+=segment.seconds;cells+=segment.actual_path_len;
    cost+=segment.actual_path_len*segment.morale_cost_per_cell;
    if(segment.morale_cost_per_cell>0)charged+=segment.actual_path_len;else free+=segment.actual_path_len;
  }
  return {total_seconds:seconds,total_actual_path_cells:cells,morale_charged_path_cells:charged,
    morale_free_path_cells:free,estimated_morale_cost:cost,estimated_arrival_morale:Math.max(0,initialMorale-cost),
    arrival_morale_source:'segment_path_cell_estimate',arrival_morale_verified:false,
    segments:JSON.parse(JSON.stringify(segments))};
}

function previewPathCells(preview,origin,target) {
  const point=p=>Array.isArray(p)&&p.length===2&&p.every(v=>Number.isInteger(v)&&v>=1&&v<=1620);
  const same=(a,b)=>point(a)&&point(b)&&a[0]===b[0]&&a[1]===b[1];
  if(!preview||typeof preview!=='object')throw Error('preview_path_invalid');
  const keys=['actual_path_len','path_length_verified','path_length_source'];
  if(!Object.hasOwn(preview,'path_waypoints')) {
    if(keys.some(k=>Object.hasOwn(preview,k)))throw Error('preview_path_proof_missing');
    return preview.path_len; // Legacy conservative estimate, never exact cells.
  }
  const path=preview.path_waypoints,nodes=preview.path_len;
  if(!Array.isArray(path)||path.length<1||path.length>4096||!path.every(point)||
      !same(path[0],origin)||!same(path[path.length-1],target)||!Number.isSafeInteger(nodes)||nodes<1||
      preview.actual_path_len!==nodes-1||preview.path_length_verified!==true||
      preview.path_length_source!=='cs_army_preview.path_nodes_minus_origin')
    throw Error('preview_path_derived_fields_invalid');
  let minimumMoves=0;
  for(let i=1;i<path.length;i++)minimumMoves+=Math.max(Math.abs(path[i][0]-path[i-1][0]),Math.abs(path[i][1]-path[i-1][1]));
  if(nodes-1<minimumMoves)throw Error('preview_path_node_count_infeasible');
  return nodes-1;
}

function validateAttack(plan,row,target,now) {
  const finite=v=>typeof v==='number'&&Number.isFinite(v);
  const point=p=>Array.isArray(p)&&p.length===2&&p.every(v=>Number.isInteger(v)&&v>=1&&v<=1620);
  const same=(a,b)=>point(a)&&point(b)&&a[0]===b[0]&&a[1]===b[1];
  if(!plan||!row||!target||!finite(now)||!finite(plan.created_at)||!finite(plan.expires_at)||
      plan.created_at>now||plan.expires_at<=now||plan.expires_at-plan.created_at>30)
    return 'attack_plan_stale';
  const a=row.army,t=plan.target,profile=attackProfile(plan);
  if(!profile)return 'attack_target_profile_invalid';
  const confirmed=a&&Array.isArray(a.status_codes)&&a.status_codes.length===1&&
    [1,3,19,43].includes(a.status_codes[0])&&a.army_status===a.status_codes[0]&&
    a.system_status===a.status_codes[0]&&a.movement_end_time===0&&a.movement_total_time===0;
  if(!a||String(a.army_id)!==plan.army_id||a.army_type!==1||
      ((a.is_tying!==false||'army_status' in a||'system_status' in a)&&!confirmed)||
      !Array.isArray(a.status_codes)||a.status_codes.length!==1||![1,3,19,43].includes(a.status_codes[0]))
    return 'army_not_ready';
  const field=plan.launch_mode==='stationary_field',base=plan.return_base_witness;
  if(field&&(!confirmed||a.status_codes[0]!==3||a.at_dispatch!==false||
      !same(a.dispatch_position,plan.dispatch_position)||same(a.position,a.dispatch_position)||
      a.command!==null||a.enemy_army_id!==null||a.fight_position!==null||a.destination!==null||
      !finite(a.observed_at)||a.observed_at>now||now-a.observed_at>30||
      plan.auto_return_mode!=='game_managed_auto_return'||plan.return_destination_verified!==false||
      plan.return_base_id!==plan.base_id||!base||base.owned!==true||base.world!==plan.world||
      base.army_id!==plan.army_id||String(base.id)!==plan.base_id||
      !same(base.position,plan.dispatch_position)||
      !['client_lua_owned_city','client_lua_owned_build'].includes(base.source)||
      !finite(base.observed_at)||base.observed_at>now||now-base.observed_at>30))
    return 'field_launch_evidence_changed';
  if(!same(a.position,plan.position)||(!field&&!same(a.position,a.dispatch_position))||
      String(a.dispatch_build_id)!==plan.base_id||!finite(a.dispatch_entered_at)||
      a.dispatch_entered_at<=0||a.dispatch_entered_at>now||
      a.movement_end_time!==0||a.movement_total_time!==0)return 'army_base_changed';
  if(!t||String(target.id)!==t.id||target.use_code!==profile.use_code||target.conf_id!==profile.conf_id||
      target.x!==t.x||target.y!==t.y||!point([target.x,target.y])||
      !finite(target.end_time)||target.end_time<=now||!finite(t.observed_at)||
      t.observed_at>now||now-t.observed_at>30)return 'target_changed_or_stale';
  const searchRadius=plan.search_radius===undefined?70:plan.search_radius;
  if(![70,140].includes(searchRadius)||Math.hypot(target.x-a.position[0],target.y-a.position[1])>searchRadius)
    return 'target_outside_authorized_search_scope';
  if(plan.defenders_verified!==true||row.defender_count!==profile.defender_count||row.cost_decree!==1||
      (plan.target_profile!==undefined&&validateAttackConfig(plan,row)!==null)||
      (plan.defenders&&plan.defenders.count!==profile.defender_count)||
      (profile.conf_id===9&&(!plan.defenders||plan.defenders.count!==2))||
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
  let chargedPath;
  try{chargedPath=previewPathCells(p,plan.position,[target.x,target.y]);}
  catch(error){return 'preview_path_unverified';}
  if(p.path_length_verified===true&&(!Number.isSafeInteger(plan.morale_charged_path_cells)||
      plan.morale_charged_path_cells!==chargedPath||!plan.route_accounting||
      typeof plan.route_accounting!=='object'||Array.isArray(plan.route_accounting)))
    return 'preview_path_accounting_required';
  if(plan.morale_charged_path_cells!==undefined&&plan.morale_charged_path_cells!==null&&
      (p.path_length_verified!==true||plan.morale_charged_path_cells!==chargedPath))
    return 'preview_path_accounting_changed';
  if(plan.route_accounting!==undefined&&plan.route_accounting!==null) {
    try {
      if(p.path_length_verified!==true)throw Error('route_path_unverified');
      const segment={kind:'attack',actual_path_len:chargedPath,seconds:p.need_time,path_verified:true,
        path_source:p.path_length_source,morale_cost_per_cell:plan.morale_cost_per_cell,
        morale_rule_verified:false,morale_rule_source:'own_army.status.cur_shiqi_cost_percell'};
      const expected=accountRoute([segment],plan.preflight_morale),actual=plan.route_accounting;
      for(const [key,value] of Object.entries(expected)) {
        if(key!=='segments'&&actual[key]!==value)throw Error('route_accounting_changed');
      }
      if(!Array.isArray(actual.segments)||actual.segments.length!==1||
          Object.entries(segment).some(([key,value])=>actual.segments[0][key]!==value)||
          plan.arrival_morale!==expected.estimated_arrival_morale)throw Error('route_accounting_changed');
    }catch(error){return 'preview_path_accounting_changed';}
  }
  if(!p||p.result!==1||!finite(p.path_len)||p.path_len<0||!finite(p.need_time)||p.need_time<0||
      p.need_energy!==15||!finite(plan.morale_cost_per_cell)||plan.morale_cost_per_cell<0||
      row.morale_cost_per_cell!==plan.morale_cost_per_cell||
      !finite(plan.min_morale)||plan.min_morale<0||!finite(plan.arrival_morale)||
      plan.arrival_morale<plan.min_morale||!finite(row.morale)||
      Math.max(0,row.morale-chargedPath*row.morale_cost_per_cell)<plan.min_morale)
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
  const profile=attackProfile(plan);
  if(!profile)throw Error('attack_target_profile_invalid');
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
    settop(L,configs);get(L,configs,'monster_npc');geti(L,top(L),profile.conf_id);const monster=top(L);
    row.defender_count=value(L,monster,'army_num');row.target_level=value(L,monster,'level');
    settop(L,configs);get(L,configs,'army_cmd');geti(L,top(L),12);const cmd=top(L);
    row.cost_decree=value(L,cmd,'cost_decree');row.cost_energy=value(L,cmd,'energy');
    row.can_auto_return=val(L,cmd,'can_auto_fallback');
    const configError=validateAttackConfig(plan,row);
    if(configError)throw Error(configError);
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
