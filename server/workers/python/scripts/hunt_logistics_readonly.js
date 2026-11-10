// Diagnostic only: names of loaded logistics fields and own-city scalar data.
// No string.dump or troop-method invocation: method extraction was not proven.
const boolean = fn('lua_toboolean','int',['pointer','int']);
function shape(L,index,scalars=false,depth=0) {
  const entries=[];pushnil(L);
  while(next(L,index)) {
    if(entries.length>=500)throw Error('logistics_shape_limit');
    const k=scalar(L,-2),i=top(L),t=type(L,i),entry={key:k,type:t};
    if(scalars && [1,3,4].includes(t))entry.value=t===1?!!boolean(L,i):scalar(L,i);
    if(scalars && depth<1 && t===5 && /troop|reserve|conscri|recruit|queue/.test(String(k)))
      entry.entries=shape(L,i,true,depth+1);
    entries.push(entry);settop(L,i-1);
  }
  return entries;
}
function inspectLogisticsModule(L,name) {
  const saved=top(L);
  try {
    get(L,-1001000,'_LOADED');get(L,-1,name);
    if(type(L,-1)!==5)return {module:name,available:false,type:type(L,-1)};
    return {module:name,available:true,entries:shape(L,top(L))};
  } finally {settop(L,saved);}
}
function ownState(L,index,depth,seen=new Set()) {
  const t=type(L,index);
  if(t===1)return !!boolean(L,index);
  if(t===3||t===4)return scalar(L,index);
  if(t!==5||depth===0)return {type:t};
  const address=pointer(L,index).toString();
  if(seen.has(address))return {cycle:true};
  seen.add(address);
  const entries=[];pushnil(L);
  while(next(L,index)) {
    if(entries.length>=100)throw Error('logistics_state_limit');
    const k=scalar(L,-2),i=top(L);
    if(type(L,i)!==6 && !['__index','__class','class','super'].includes(k))
      entries.push({key:k,value:ownState(L,i,depth-1,seen)});
    settop(L,i-1);
  }
  seen.delete(address);return entries;
}
function logisticsRead(L) {
  send({kind:'diagnostic_stage',stage:'module_fields'});
  const modules=['logic.conscription.conscription_mgr','logic.conscription.barrack_data',
                 'logic.player.army.my_army_mgr'].map(name=>inspectLogisticsModule(L,name));
  send({kind:'diagnostic_partial',stage:'module_fields',data:modules});
  send({kind:'diagnostic_stage',stage:'own_conscription_state'});
  const base=top(L),mgr=module(L,'logic.conscription.conscription_mgr'),state={};
  for(const name of ['barrack_infos','army_conscription_infos','conscription_info_dict','person_guard_data','build_quota_infos']) {
    get(L,mgr,name);state[name]=ownState(L,top(L),4);settop(L,mgr);
  }
  const heroMgr=module(L,'logic.hero.hero_mgr');
  get(L,heroMgr,'hero_self_select_data');state.hero_self_select_data=ownState(L,top(L),4);settop(L,heroMgr);
  get(L,heroMgr,'hero_temporary_mgr');state.hero_temporary_state=ownState(L,top(L),3);settop(L,heroMgr);
  settop(L,base);
  const armyMgr=module(L,'logic.player.army.my_army_mgr');get(L,armyMgr,'army_id_tbl');
  const armies=top(L);state.army_reserve_flags=[];pushnil(L);
  while(next(L,armies)) {
    const a=top(L),entry={army_id:value(L,a,'army_id'),fields:shape(L,a)};
    for(const name of ['is_person_guard','person_guard_index']) {get(L,a,name);entry[name]=ownState(L,top(L),1);settop(L,a);}
    get(L,a,'status');
    if(type(L,-1)===5) {
      const st=top(L);entry.status={};
      for(const name of ['is_person_guard','person_guard_index']) {get(L,st,name);entry.status[name]=ownState(L,top(L),1);settop(L,st);}
    }
    state.army_reserve_flags.push(entry);settop(L,a-1);
  }
  settop(L,base);
  send({kind:'diagnostic_partial',stage:'own_conscription_state',data:state});
  send({kind:'diagnostic_stage',stage:'own_city_fields'});
  const saved=top(L),player=module(L,'logic.player.player_mgr');
  get(L,player,'city');get(L,-1,'cities');const list=top(L),cities=[];pushnil(L);
  while(next(L,list)) {
    if(cities.length>=10)throw Error('logistics_city_limit');
    const city=top(L),id=value(L,city,'city_id');
    cities.push({city_id:id,entries:shape(L,city,true)});settop(L,city-1);
  }
  settop(L,saved);return {modules,cities,state,gameplay_commands:0};
}
let busy=false,finished=false;
lifecycle.attach(client.getExportByName('lua_pcallk'),{onEnter(args){
  if(busy||finished)return;busy=true;const L=args[0],saved=top(L);
  try {
    if(!checkstack(L,256))throw Error('logistics_stack_capacity');
    identity(L);const result=logisticsRead(L);settop(L,saved);identity(L);
    lifecycle.finish({kind:'complete',data:result});finished=true;
  } catch(error){lifecycle.finish({kind:'failure',reason:String(error)});finished=true;}
  finally{settop(L,saved);busy=false;}
}});
