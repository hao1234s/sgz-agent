// Fixed read-only operations. Runs on the game's Lua call thread, restoring its stack.
const client = Process.getModuleByName('S3Client.exe');
if (client.path.replaceAll('\\', '/').toLowerCase() !== OPTIONS.path) throw Error('api_game_path_mismatch');
const lifecycle = createNativeLifecycle({
  attach:(address,handler)=>Interceptor.attach(address,handler),
  flush:()=>Interceptor.flush(), defer:fn=>setImmediate(fn), publish:message=>send(message)
});
rpc.exports = {close:()=>lifecycle.close(), state:()=>lifecycle.state()};
function fn(n, r, a) { return new NativeFunction(client.getExportByName(n), r, a); }
const top = fn('lua_gettop', 'int', ['pointer']);
const checkstack = fn('lua_checkstack', 'int', ['pointer', 'int']);
const settop = fn('lua_settop', 'void', ['pointer', 'int']);
const field = fn('lua_getfield', 'int', ['pointer', 'int', 'pointer']);
const pushvalue = fn('lua_pushvalue', 'void', ['pointer', 'int']);
const pushinteger = fn('lua_pushinteger', 'void', ['pointer', 'int64']);
const pushstring = fn('lua_pushstring', 'pointer', ['pointer', 'pointer']);
const call = fn('lua_pcallk', 'int', ['pointer', 'int', 'int', 'int', 'int64', 'pointer']);
const type = fn('lua_type', 'int', ['pointer', 'int']);
const string = fn('lua_tolstring', 'pointer', ['pointer', 'int', 'pointer']);
const number = fn('lua_tonumberx', 'double', ['pointer', 'int', 'pointer']);
const pushnil = fn('lua_pushnil', 'void', ['pointer']);
const next = fn('lua_next', 'int', ['pointer', 'int']);
const pointer = fn('lua_topointer', 'pointer', ['pointer', 'int']);
const cfunction = fn('lua_tocfunction', 'pointer', ['pointer', 'int']);
const upvalue = fn('lua_getupvalue', 'pointer', ['pointer', 'int', 'int']);
const names = new Map();
function key(name) { if (!names.has(name)) names.set(name, Memory.allocUtf8String(name)); return names.get(name); }
function get(L, i, name) {
  if (i !== -1001000 && type(L, i) !== 5) throw Error('api_table_unavailable:' + name);
  return field(L, i, key(name));
}
function scalar(L, i) {
  if (type(L, i) === 3) return number(L, i, ptr(0));
  if (type(L, i) === 4) return string(L, i, ptr(0)).readUtf8String().slice(0, 200);
  return null;
}
function value(L, i, name) { get(L, i, name); const v = scalar(L, -1); settop(L, top(L) - 1); return v; }
function module(L, name) {
  if (get(L, -1001000, '_LOADED') !== 5 || get(L, -1, name) !== 5) throw Error('api_module_unavailable:' + name);
  return top(L);
}
function invoke(L, manager, name, coordinates) {
  if (get(L, manager, name) !== 6) throw Error('api_method_unavailable:' + name);
  pushvalue(L, manager);
  for (const c of coordinates || []) pushinteger(L, c);
  if (call(L, 1 + (coordinates || []).length, 1, 0, 0, ptr(0)) !== 0) throw Error('api_read_failed:' + name);
  return top(L);
}
function identity(L) {
  const saved = top(L), p = module(L, 'logic.player.player_mgr');
  const role = String(value(L, p, 'player_id'));
  if (get(L, p, 'cur_server') !== 5) throw Error('api_login_required');
  const s = top(L), server = value(L, s, 'server_id'), region = value(L, s, 'name'), season = value(L, s, 'season');
  settop(L, saved);
  if (role !== OPTIONS.role_id || server !== OPTIONS.server_id || region !== OPTIONS.region || season !== OPTIONS.season)
    throw Error('api_identity_mismatch');
  return {role_id: role, server_id: server, region, season};
}

// The status path never invokes cs_get_army_status (its response contains morale).
const boolean = fn('lua_toboolean', 'int', ['pointer', 'int']);
const rawi = fn('lua_rawgeti', 'int', ['pointer', 'int', 'int64']);
function val(L, i, name) { get(L, i, name); const v = type(L, -1) === 1 ? !!boolean(L, -1) : scalar(L, -1); settop(L, top(L)-1); return v; }
function position(L, i, name) {
  get(L, i, name); const v = type(L, -1) === 5 ? [value(L, top(L), 'row'), value(L, top(L), 'col')] : null;
  settop(L, top(L)-1); return v;
}
function ints(L, i, name) {
  get(L, i, name); const out = [];
  if (type(L, -1) === 5) {
    const list = top(L); pushnil(L);
    while (next(L, list)) { if (out.length >= 32) throw Error('hunt_list_limit'); out.push(scalar(L, -1)); settop(L, top(L)-1); }
  }
  settop(L, top(L)-1); return out;
}
function statusOnly(L, army) {
  const row = {observed_at: Date.now()/1000};
  for (const name of ['army_id', 'city_id', 'dispatch_build_id', 'is_tying']) row[name] = val(L, army, name);
  row.army_type = value(L, army, '__army_type');
  row.hero_ids = ints(L, army, 'hero_ids');
  row.status_codes = []; row.position = null; row.at_dispatch = false;
  if (row.army_type !== 1) return row;
  row.dispatch_position = position(L, army, 'dispatch_build_pos');
  row.dispatch_entered_at = value(L, army, 'enter_dispatch_time');
  if (get(L, army, 'status') !== 5) throw Error('hunt_status_missing');
  const st = top(L);
  row.status_codes = ints(L, st, 'status');
  row.position = position(L, st, 'pos');
  // Legacy status.dispatch_pos can retain the home city after deployment.
  const dispatch = row.dispatch_position;
  row.at_dispatch = row.position && dispatch && row.position[0] === dispatch[0] && row.position[1] === dispatch[1];
  row.movement_end_time = value(L, st, 'end_time');
  row.movement_total_time = value(L, st, 'total_time');
  row.command = value(L, st, 'cmd');
  row.destination = position(L, st, 'end_pos');
  row.auto_return = val(L, st, 'is_auto_fallback');
  row.finish_success = val(L, st, 'finish_success');
  row.monster_type = value(L, st, 'monster_type');
  row.monster_config_id = value(L, st, 'monster_config_id');
  row.fight_position = position(L, st, 'fight_pos');
  // No troop, morale, battle-report, target-map or hero-manager reads here.
  row.enemy_army_id = value(L, st, 'enemy_army_id');
  settop(L, army);
  return row;
}
function isReady(row) {
  if(row.army_type!==1 || row.is_tying===true || row.status_codes.length!==1)return false;
  if([1,3,43].includes(row.status_codes[0]))return true;
  const p=row.position,d=row.dispatch_position;
  return row.status_codes[0]===19 && Array.isArray(p) && p.length===2 &&
    p.every(v=>Number.isInteger(v)&&v>=1&&v<=1620) && Array.isArray(d) && d.length===2 &&
    p[0]===d[0] && p[1]===d[1] && Number.isSafeInteger(row.dispatch_build_id) && row.dispatch_build_id>0 &&
    Number.isFinite(row.dispatch_entered_at) && row.dispatch_entered_at>0 &&
    Number.isFinite(row.observed_at) && row.dispatch_entered_at<=row.observed_at &&
    row.movement_end_time===0 && row.movement_total_time===0;
}
function roster(L) {
  const mgr = module(L, 'logic.player.army.my_army_mgr');
  if (get(L, mgr, 'army_id_tbl') !== 5) throw Error('hunt_roster_missing');
  const list = top(L), armies = []; pushnil(L);
  while (next(L, list)) {
    if (armies.length >= 100 || type(L, -1) !== 5) throw Error('hunt_roster_invalid');
    const army = top(L); armies.push(statusOnly(L, army)); settop(L, army-1);
  }
  return {complete: true, armies, observed_at: Date.now()/1000};
}
function readyArmy(L) {
  const mgr = module(L, 'logic.player.army.my_army_mgr'); get(L, mgr, 'army_id_tbl'); const list = top(L);
  if (rawi(L, list, OPTIONS.army_id) !== 5) throw Error('hunt_army_not_owned');
  const army = top(L), row = statusOnly(L, army);
  if (!isReady(row)) throw Error('army_not_ready_for_details');
  const moraleIndex = invoke(L, army, 'get_cur_shiqi');
  const morale = scalar(L, moraleIndex); settop(L, army);
  const heroes = [], heroMgr = module(L, 'logic.hero.hero_mgr'); get(L, heroMgr, 'hero_dict'); const dict = top(L);
  for (let slot = 1; slot <= 3; slot++) {
    get(L, army, 'hero_ids'); rawi(L, top(L), slot); const id = scalar(L, -1); settop(L, dict);
    if (!Number.isInteger(id) || id <= 0) continue;
    if (rawi(L, dict, id) !== 5) throw Error('hunt_hero_missing');
    const h = top(L), item = {id: String(id), is_main: slot === 1, slot};
    item.troops = value(L, h, 'troops'); item.capacity = value(L, h, 'max_troops');
    item.stamina = value(L, h, 'energy'); item.wounded_until = value(L, h, 'wounded_time');
    heroes.push(item); settop(L, dict);
  }
  const stamina = heroes.length ? Math.min(...heroes.map(h=>h.stamina)) : null;
  return {army: row, heroes, morale, stamina, observed_at: Date.now()/1000};
}
function bases(L) {
  const p = module(L, 'logic.player.player_mgr'); get(L, p, 'city'); get(L, -1, 'cities');
  const cities = top(L), result = []; pushnil(L);
  while (next(L, cities)) {
    const b = top(L), id = value(L, b, 'city_id'), kind = value(L, b, 'city_type');
    if (id && [1,2].includes(kind)) result.push({id: String(id), kind: kind === 1 ? 'main' : 'sub',
      position: position(L,b,'pos'), name:value(L,b,'name'), available:false, can_recruit:null,
      capacity_available:null, eligibility_verified:false, eligible_army_ids:[], source:'client_lua_owned_city'});
    settop(L,b-1);
  }
  get(L,p,'build'); get(L,-1,'builds'); const buildings = top(L); pushnil(L); let count = 0;
  while(next(L,buildings)) {
    if (++count > 1000) throw Error('hunt_base_limit');
    const b=top(L), kind=value(L,b,'build_type');
    if ([6,139].includes(kind) && val(L,b,'is_alliance') === false) result.push({
      id:String(value(L,b,'build_id')), kind:'camp', position:position(L,b,'pos'), name:value(L,b,'name'),
      available:false, can_recruit:null, capacity_available:null, eligibility_verified:false,
      eligible_army_ids:[], source:'client_lua_owned_build', build_type:kind,
      creating:val(L,b,'is_creating'), destroying:val(L,b,'is_destroying')});
    settop(L,b-1);
  }
  return {bases:result, observed_at:Date.now()/1000};
}
function cachedGiants(L) {
  const mgr=module(L,'logic.map.npc.map_npc_mgr'); get(L,mgr,'uid_npc_data'); const list=top(L), targets=[]; pushnil(L); let count=0;
  while(next(L,list)) {
    if(++count>10000)throw Error('hunt_npc_limit'); const e=top(L);
    if(type(L,e)===5 && get(L,e,'aoi_info')===5) {
      const a=top(L), id=value(L,a,'id');
      if(get(L,a,'monster')===5) {
        const m=top(L), use=value(L,m,'use_code'), conf=value(L,m,'conf_id');
        if(use===1&&conf===10) {const pos=position(L,m,'pos');if(pos)targets.push({id,x:pos[0],y:pos[1],use_code:use,conf_id:conf,end_time:value(L,m,'end_time')});}
      }
    }
    settop(L,e-1);
  }
  return {complete:false, targets, source:'client_aoi_cache', coverage_reason:'server_region_coverage_unverified', observed_at:Date.now()/1000};
}
// Read-only, schema-identified preview. This is not a generic network.call API.
// A response has no request ID of its own; concurrent preview traffic invalidates
// correlation. Returned timings remain diagnostic until the full route is proven.
function decodedReturnIndex(stackTop, resultCount) {
  if (!Number.isInteger(stackTop) || !Number.isInteger(resultCount) ||
      resultCount < 1 || resultCount > 3 || stackTop < resultCount)
    throw Error('hunt_preview_decode_return_count');
  return stackTop - resultCount + 1;
}
let previewStarted=false, previewData=null, previewAmbiguous=false, ownPreviewCall=false;
function startPreview(L) {
  const request=OPTIONS.request;
  if (!request || !Number.isSafeInteger(request.army_id) || request.army_id<=0 ||
      ![11,12].includes(request.cmd) || request.cmd_target_type!==1) throw Error('hunt_preview_invalid');
  function validPos(p) {return p && Number.isInteger(p.row)&&Number.isInteger(p.col)&&p.row>=1&&p.row<=1620&&p.col>=1&&p.col<=1620;}
  if(!validPos(request.target_pos)||(request.from_pos&&!validPos(request.from_pos)))throw Error('hunt_preview_position_invalid');
  const mgr=module(L,'logic.player.army.my_army_mgr');get(L,mgr,'army_id_tbl');
  if(rawi(L,top(L),request.army_id)!==5)throw Error('hunt_army_not_owned');
  if(!isReady(statusOnly(L,top(L))))throw Error('army_not_ready_for_preview');
  get(L,-1001000,'_LOADED');const loaded=top(L);get(L,loaded,'network.network');const factory=top(L);
  let found=false;
  for(let n=1;n<=40;n++) {const p=upvalue(L,factory,n);if(p.isNull())break;if(p.readUtf8String()==='Global'){found=true;break;}settop(L,factory);}
  if(!found)throw Error('api_network_unavailable');
  get(L,-1,'network');const network=top(L);get(L,network,'rpc');get(L,-1,'v_server_proto');get(L,-1,'__cobj');const schema=top(L);
  get(L,loaded,'sproto.core');const core=top(L);
  get(L,core,'protocol');pushvalue(L,schema);pushstring(L,key('cs_army_preview'));
  if(call(L,2,3,0,0,ptr(0))!==0 || scalar(L,-3)!==502 || type(L,-2)!==2 || type(L,-1)!==2)throw Error('api_protocol_version_mismatch');
  const reqType=pointer(L,-2).toString(), resType=pointer(L,-1).toString();settop(L,core);
  const hooked=new Set();
  for(const name of ['encode','encodep']) {
    get(L,core,name);const address=cfunction(L,-1);settop(L,core);
    if(address.isNull())throw Error('api_codec_unavailable');
    if(hooked.has(address.toString()))continue;hooked.add(address.toString());
    lifecycle.attach(address,{onEnter(args) {
      if(!finished&&pointer(args[0],1).toString()===reqType&&!ownPreviewCall)previewAmbiguous=true;
    }});
  }
  for(const name of ['decode','decodep','decoden']) {
    get(L,core,name);const address=cfunction(L,-1);settop(L,core);
    if(address.isNull())throw Error('api_codec_unavailable');
    if(hooked.has(address.toString()))continue;hooked.add(address.toString());
    lifecycle.attach(address,{onEnter(args) {this.state=args[0];this.match=pointer(this.state,1).toString()===resType;},onLeave(ret) {
      if(!this.match||finished)return;const state=this.state,saved=top(state);
      try {
        const resultIndex=decodedReturnIndex(saved,ret.toInt32());
        if(!checkstack(state,32)||type(state,resultIndex)!==5)throw Error('hunt_preview_response_invalid');
        const response={};
        for(const k of ['result','need_time','prepare_time','path_len','speed','need_energy','need_mp','speed_up_factor','rush_len'])response[k]=value(state,resultIndex,k);
        if(previewData)previewAmbiguous=true;
        previewData=response;
      }catch(e){lifecycle.finish({kind:'failure',reason:String(e.message||e)});finished=true;}
      finally{settop(state,saved);}
    }});
  }
  const create=fn('lua_createtable','void',['pointer','int','int']);
  const set=fn('lua_setfield','void',['pointer','int','pointer']);
  function pushPos(p){create(L,0,2);const i=top(L);for(const k of ['row','col']){pushinteger(L,p[k]);set(L,i,key(k));}}
  get(L,network,'call');pushvalue(L,network);pushstring(L,key('cs_army_preview'));create(L,0,5);const args=top(L);
  for(const k of ['army_id','cmd','cmd_target_type']){pushinteger(L,request[k]);set(L,args,key(k));}
  pushPos(request.target_pos);set(L,args,key('target_pos'));
  if(request.from_pos){pushPos(request.from_pos);set(L,args,key('from_pos'));}
  ownPreviewCall=true;
  try{if(call(L,3,0,0,0,ptr(0))!==0)throw Error('hunt_preview_query_failed');}
  finally{ownPreviewCall=false;}
  previewStarted=true;
}
let busy=false, finished=false;
lifecycle.attach(client.getExportByName('lua_pcallk'), {onEnter(args) {
  if(busy||finished)return; busy=true; const L=args[0], saved=top(L);
  try {
    if(!checkstack(L,128))throw Error('api_lua_stack_capacity'); identity(L); let result;
    if(OPTIONS.operation==='roster')result=roster(L);
    else if(OPTIONS.operation==='ready')result=readyArmy(L);
    else if(OPTIONS.operation==='bases')result=bases(L);
    else if(OPTIONS.operation==='search')result=cachedGiants(L);
    else if(OPTIONS.operation==='preview') {
      if(!previewStarted)startPreview(L);
      if(previewAmbiguous)throw Error('hunt_preview_correlation_ambiguous');
      if(!previewData)return;
      result={preview:previewData,correlation:'single_observed_request',observed_at:Date.now()/1000};
    }
    else throw Error('hunt_operation_not_allowed');
    settop(L,saved);identity(L);lifecycle.finish({kind:'complete',data:result});finished=true;
  } catch(e) {lifecycle.finish({kind:'failure',reason:String(e.message||e)});finished=true;}
  finally {settop(L,saved);busy=false;}
}});
