// Passive, schema-whitelisted observer. Does not send or invoke gameplay commands.
// Loaded after hunt_client.js helpers, without its default operation hook.
let busy=false,finished=false,installed=false;
const observed=[],requestTypes=new Map(),responseTypes=new Map();
let settleTimer=null;
function stopObservation(error) {
  if(finished)return;
  finished=true;
  if(settleTimer!==null)clearTimeout(settleTimer);
  lifecycle.finish(error?{kind:'failure',reason:String(error)}:
    {kind:'complete',data:{observed,observer_generated_commands:0}});
}
function fields(L,index,definition) {
  if(type(L,index)!==5)throw Error('replenish_observer_payload_not_table');
  const result={};
  for(const name of definition.fields)
    result[name]=name.endsWith('_list')?ints(L,index,name):val(L,index,name);
  return result;
}
function observe(L,index,definition,direction) {
  if(finished)return;
  if(observed.length>=30)throw Error('replenish_observer_packet_limit');
  const item={direction,name:definition.name,data:fields(L,index,definition),epoch:Date.now()/1000};
  observed.push(item);send({kind:'diagnostic_observation',data:item});
  if(direction==='response' && observed.some(r=>r.direction==='request'&&r.name==='cs_army_set_troops')) {
    if(settleTimer!==null)clearTimeout(settleTimer);
    settleTimer=setTimeout(()=>stopObservation(),20000);
  }
}
function install(L) {
  get(L,-1001000,'_LOADED');const loaded=top(L);
  get(L,loaded,'network.network');const factory=top(L);let found=false;
  for(let n=1;n<=40;n++) {
    const p=upvalue(L,factory,n);if(p.isNull())break;
    if(p.readUtf8String()==='Global'){found=true;break;}settop(L,factory);
  }
  if(!found)throw Error('replenish_observer_network_unavailable');
  get(L,-1,'network');get(L,-1,'rpc');const rpcIndex=top(L);
  get(L,rpcIndex,'v_server_proto');get(L,-1,'__cobj');const server=top(L);
  get(L,rpcIndex,'v_client_proto');get(L,-1,'__cobj');const receiver=top(L);
  const core=module(L,'sproto.core');
  const definitions=[
    {schema:server,id:902,name:'cs_army_set_troops',fields:['army_id','hero_id_list','hero_troop_list','cons_index'],response:['result']},
    {schema:server,id:1751,name:'cs_barrack_conscription_begin',fields:['city_id','target_troops','cons_index','use_free_quota']},
    {schema:server,id:1760,name:'cs_barrack_conscription_full',fields:['city_id','cons_index','use_free_quota']},
    {schema:receiver,id:1091,name:'sc_army_set_troops',fields:['result','army_id']},
    {schema:receiver,id:1701,name:'sc_barrack_conscription_begin',fields:['city_id','target_troops','speed','begin_time','left_troops','is_full','cons_index','cur_free_quota','rec_free_quota']},
    {schema:receiver,id:1703,name:'sc_barrack_troops_parameters',fields:['city_id','cur_troops','max_troops','speed','cons_index','cur_free_quota','rec_free_quota']},
    {schema:receiver,id:1706,name:'sc_barrack_conscription_update',fields:['city_id','target_troops','speed','begin_time','left_troops','cons_index','cur_free_quota','rec_free_quota']}
  ];
  for(const definition of definitions) {
    get(L,core,'protocol');pushvalue(L,definition.schema);pushstring(L,key(definition.name));
    if(call(L,2,3,0,0,ptr(0))!==0||scalar(L,-3)!==definition.id||type(L,-2)!==2)
      throw Error('replenish_observer_schema_mismatch:'+definition.name);
    const destination=definition.schema===server?requestTypes:responseTypes;
    destination.set(pointer(L,-2).toString(),definition);
    if(definition.response) {
      if(type(L,-1)!==2)throw Error('replenish_observer_response_missing');
      responseTypes.set(pointer(L,-1).toString(),{name:definition.name+'.response',fields:definition.response});
    }
    settop(L,core);
  }
  const addresses=new Set();
  for(const name of ['encode','encodep','decode','decodep','decoden']) {
    get(L,core,name);const address=cfunction(L,-1);settop(L,core);
    if(address.isNull())throw Error('replenish_observer_codec_unavailable');
    if(addresses.has(address.toString()))continue;addresses.add(address.toString());
    const encoding=name.startsWith('encode');
    lifecycle.attach(address,{
      onEnter(args) {
        this.state=args[0];this.definition=null;
        if(finished)return;
        const state=this.state,saved=top(state);
        try {
          this.definition=(encoding?requestTypes:responseTypes).get(pointer(state,1).toString());
          if(this.definition&&encoding) {
            if(!checkstack(state,64))throw Error('replenish_observer_stack_capacity');
            observe(state,2,this.definition,'request');
          }
        } catch(error){stopObservation(error);}
        finally{settop(state,saved);}
      },
      onLeave(ret) {
        if(encoding||!this.definition||finished)return;
        const state=this.state,saved=top(state);
        try {
          if(!checkstack(state,64))throw Error('replenish_observer_stack_capacity');
          observe(state,decodedReturnIndex(saved,ret.toInt32()),this.definition,'response');
        } catch(error){stopObservation(error);}
        finally{settop(state,saved);}
      }
    });
  }
  send({kind:'diagnostic_stage',stage:'passive_replenish_observer_ready'});
  setTimeout(()=>stopObservation(),110000);
}
lifecycle.attach(client.getExportByName('lua_pcallk'),{onEnter(args){
  if(busy||finished||installed)return;busy=true;const L=args[0],saved=top(L);
  try {
    if(!checkstack(L,256))throw Error('replenish_observer_stack_capacity');
    identity(L);install(L);settop(L,saved);identity(L);installed=true;
  } catch(error){stopObservation(error);}
  finally{settop(L,saved);busy=false;}
}});
