// Fixed passive hunt protocol projections; never accumulates packet history.
const HEO_SCALAR = 'scalar', HEO_POS = {row:'scalar',col:'scalar'};
const HEO_EXTRA = {interrupt_guard:'scalar',cancel_conscription:'scalar',
  target_army_id:'scalar',dont_continue_job:'scalar',target_eid:'scalar',target_level:'scalar',
  schedule_attack_city:{target_pos:HEO_POS,schedule_type:'scalar',is_auto_fallback:'scalar'}};
const HEO_STATUS = {status:['scalar'],pos:HEO_POS,start_pos:HEO_POS,end_pos:HEO_POS,
  dispatch_pos:HEO_POS,enemy_pos:HEO_POS,fight_pos:HEO_POS,end_building_pos:HEO_POS,
  path_len:'scalar',speed:'scalar',next_pos_time:'scalar',end_time:'scalar',total_time:'scalar',
  arrive_time:'scalar',active_fallback:'scalar',enemy_army_id:'scalar',finish_success:'scalar',
  monster_type:'scalar',monster_config_id:'scalar',cmd:'scalar',is_auto_fallback:'scalar',
  is_tied:'scalar',draw_state:'scalar'};
const HEO_ENTITY = {eid:'scalar',
  aoi:{aoi_id:'scalar',aoi_type:'scalar'},
  player_army:{aoi_id:'scalar',army_sid:'scalar',cur_path_idx:'scalar',is_moving:'scalar',pos:HEO_POS},
  state:{last_state:'scalar',state:'scalar'},
  army_unit:{target_pos:HEO_POS,army_cmd:'scalar',is_active_fallback:'scalar',
    is_auto_fallback:'scalar',finish_success:'scalar',is_tied:'scalar',is_tying:'scalar'},
  fighter:{enemy_aoi_id:'scalar',enemy_fighter_type:'scalar',enemy_monster_cfg_id:'scalar',
    enemy_monster_type:'scalar',enemy_pos:HEO_POS,fight_draw_duration:'scalar',fight_draw_end_time:'scalar',
    fight_refuse_mark:'scalar',is_fight_draw:'scalar',is_fighting:'scalar',is_tied:'scalar',
    is_tying:'scalar',draw_state:'scalar'},
  position:{arrive_time:'scalar',last_pos:HEO_POS,pos:HEO_POS},
  move:{cur_pos_idx:'scalar',cur_speed:'scalar',end_time:'scalar',is_moving:'scalar',
    is_paused:'scalar',is_rushing:'scalar',next_turning_point_idx:'scalar',
    pass_path_used_time:'scalar',reach_next_pos_time:'scalar',speed:'scalar',
    target_pos:HEO_POS,total_time:'scalar',start_pos:HEO_POS}};
const HEO_DEFINITIONS = [
  {side:'server',id:501,name:'cs_army_cmd',shape:{cmd:'scalar',army_id:'scalar',target_pos:HEO_POS,
    times:'scalar',is_auto_fallback:'scalar',path_pos:[HEO_POS],cmd_target_type:'scalar',
    is_occupy:'scalar',ext_str:'scalar',path_index:'scalar',ext_int:'scalar',extra_data:HEO_EXTRA,
    release_pos:HEO_POS},response:{result:'scalar'}},
  {side:'server',id:502,name:'cs_army_preview',shape:{army_id:'scalar',target_pos:HEO_POS,cmd:'scalar',
    cmd_target_type:'scalar',ext_int:'scalar',extra_data:HEO_EXTRA,from_pos:HEO_POS,is_reserve:'scalar'},
    response:{result:'scalar',need_time:'scalar',prepare_time:'scalar',path_len:'scalar',speed:'scalar',
      need_energy:'scalar',need_mp:'scalar',speed_up_factor:'scalar',rush_len:'scalar',path:[HEO_POS]}},
  {side:'server',id:956,name:'cs_return_to_dispatch',shape:{army_id:'scalar',target_type:'scalar'},response:{result:'scalar'}},
  {side:'client',id:1003,name:'sc_sync_army_status',shape:{army_info:{army_id:'scalar',city_id:'scalar',
    dispatch_build_id:'scalar',dispatch_build_pos:HEO_POS,enter_dispatch_time:'scalar',is_tying:'scalar',status:HEO_STATUS}}},
  {side:'client',id:1041,name:'sc_army_battle_result_notity',shape:{armyid:'scalar',result:'scalar',pos:HEO_POS,
    enemy_army_id:'scalar',monster_type:'scalar',monster_config_id:'scalar',is_water_battle:'scalar'}},
  {side:'client',id:7715,name:'sc_army_unit_update',shape:{entity:HEO_ENTITY}},
  {side:'client',id:7709,name:'sc_army_pos_change',shape:{army_id:'scalar',pos:HEO_POS}},
  // Outgoing refill targets and barrack pools are projected from these fixed
  // packets only. No active hero object or hero/troop refresh is accessed.
  {side:'server',id:902,name:'cs_army_set_troops',shape:{army_id:'scalar',hero_id_list:['scalar'],
    hero_troop_list:['scalar'],cons_index:'scalar'},response:{result:'scalar'}},
  {side:'server',id:1751,name:'cs_barrack_conscription_begin',shape:{city_id:'scalar',
    target_troops:'scalar',cons_index:'scalar',use_free_quota:'scalar'}},
  {side:'server',id:1760,name:'cs_barrack_conscription_full',shape:{city_id:'scalar',
    cons_index:'scalar',use_free_quota:'scalar'}},
  {side:'client',id:1091,name:'sc_army_set_troops',shape:{result:'scalar',army_id:'scalar'}},
  {side:'client',id:1701,name:'sc_barrack_conscription_begin',shape:{city_id:'scalar',target_troops:'scalar',
    speed:'scalar',begin_time:'scalar',left_troops:'scalar',is_full:'scalar',cons_index:'scalar',
    cur_free_quota:'scalar',rec_free_quota:'scalar'}},
  {side:'client',id:1703,name:'sc_barrack_troops_parameters',shape:{city_id:'scalar',cur_troops:'scalar',
    max_troops:'scalar',speed:'scalar',cons_index:'scalar',cur_free_quota:'scalar',rec_free_quota:'scalar'}},
  {side:'client',id:1706,name:'sc_barrack_conscription_update',shape:{city_id:'scalar',target_troops:'scalar',
    speed:'scalar',begin_time:'scalar',left_troops:'scalar',cons_index:'scalar',
    cur_free_quota:'scalar',rec_free_quota:'scalar'}}
];
function heoRead(L,index,shape,budget) {
  if(--budget.nodes<0)throw Error('hunt_observer_payload_budget');
  const saved=top(L),t=type(L,index);
  try {
    if(shape===HEO_SCALAR) {
      if(t===1)return !!boolean(L,index);
      if(t===3||t===4)return scalar(L,index);
      return {unread_type:t};
    }
    if(t!==5)throw Error('hunt_observer_payload_not_table');
    if(Array.isArray(shape)) {
      const out=[];let count=0;pushnil(L);
      while(next(L,index)) {
        const slot=scalar(L,-2);
        if(++count>128||!Number.isInteger(slot)||slot<1||slot>128)throw Error('hunt_observer_list_limit');
        const i=top(L);out[slot-1]=heoRead(L,i,shape[0],budget);settop(L,i-1);
      }
      if(count!==out.length)throw Error('hunt_observer_sparse_list');
      return out;
    }
    const out={};
    for(const name of Object.keys(shape)) {
      get(L,index,name);
      if(type(L,-1)!==0)out[name]=heoRead(L,top(L),shape[name],budget);
      settop(L,saved);
    }
    return out;
  } finally {settop(L,saved);}
}

function createHuntObserver() {
  const requests=new Map(), responses=new Map();
  let installed=false, closing=false, sequence=0, windowStart=Date.now(), windowCount=0;
  function record(L,index,definition,direction) {
    if(closing)return;
    const now=Date.now();
    if(now-windowStart>=60000){windowStart=now;windowCount=0;}
    if(++windowCount>1200)throw Error('hunt_observer_event_rate_limit');
    identity(L);
    const data=heoRead(L,index,definition.shape,{nodes:800});
    runtimePublishEvent({kind:'protocol_event',sequence:++sequence,direction,
      name:definition.name,data,epoch:now/1000});
  }
  function install(L) {
    if(installed||closing)throw Error('hunt_observer_install_state_invalid');
    get(L,-1001000,'_LOADED');const loaded=top(L);
    get(L,loaded,'network.network');const factory=top(L);let found=false;
    for(let n=1;n<=40;n++) {
      const p=upvalue(L,factory,n);if(p.isNull())break;
      if(p.readUtf8String()==='Global'){found=true;break;}settop(L,factory);
    }
    if(!found)throw Error('hunt_observer_network_unavailable');
    get(L,-1,'network');get(L,-1,'rpc');const rpcIndex=top(L);
    get(L,rpcIndex,'v_server_proto');get(L,-1,'__cobj');const server=top(L);
    get(L,rpcIndex,'v_client_proto');get(L,-1,'__cobj');const receiver=top(L);
    const core=module(L,'sproto.core');
    for(const definition of HEO_DEFINITIONS) {
      get(L,core,'protocol');pushvalue(L,definition.side==='server'?server:receiver);pushstring(L,key(definition.name));
      if(call(L,2,3,0,0,ptr(0))!==0||scalar(L,-3)!==definition.id||type(L,-2)!==2)
        throw Error('hunt_observer_schema_mismatch:'+definition.name);
      (definition.side==='server'?requests:responses).set(pointer(L,-2).toString(),definition);
      if(definition.response) {
        if(type(L,-1)!==2)throw Error('hunt_observer_response_missing:'+definition.name);
        responses.set(pointer(L,-1).toString(),{name:definition.name+'.response',shape:definition.response});
      }
      settop(L,core);
    }
    const addresses=new Set();
    for(const name of ['encode','encodep','decode','decodep','decoden']) {
      get(L,core,name);const address=cfunction(L,-1);settop(L,core);
      if(address.isNull())throw Error('hunt_observer_codec_unavailable');
      if(addresses.has(address.toString()))continue;addresses.add(address.toString());
      const encoding=name.startsWith('encode');
      lifecycle.attach(address,{onEnter(args) {
        this.state=args[0];this.definition=null;if(closing)return;
        const state=this.state,saved=top(state);
        try {
          this.definition=(encoding?requests:responses).get(pointer(state,1).toString());
          if(this.definition&&encoding) {
            if(!checkstack(state,128))throw Error('hunt_observer_stack_capacity');
            record(state,2,this.definition,'request');
          }
        }catch(error){runtimeFail(error);}
        finally{settop(state,saved);}
      },onLeave(ret) {
        if(encoding||!this.definition||closing)return;
        const state=this.state,saved=top(state);
        try {
          if(!checkstack(state,128))throw Error('hunt_observer_stack_capacity');
          record(state,decodedReturnIndex(saved,ret.toInt32()),this.definition,'response');
        }catch(error){runtimeFail(error);}
        finally{settop(state,saved);}
      }});
    }
    installed=true;
  }
  return {install,close(){closing=true;},state(){return {installed,closing,sequence};}};
}
