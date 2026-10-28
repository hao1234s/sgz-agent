// Loaded after native_lifecycle + hunt_client helpers (without their main hook)
// and hunt_search.js. Only fixed read-only 519 requests are ever constructed.
function refreshEncodeMatches(actual,expected,ownCall,sameThread,seen) {
  return ownCall===true&&sameThread===true&&seen===0&&actual&&expected&&
    ['row','col','session_id'].every(k=>Number.isSafeInteger(actual[k])&&actual[k]===expected[k]);
}

function staticResourceTable(L,name,api) {
  if(!['S1.cn.res_pro.map_attr','S1.cn.res_pro.aoi_attr'].includes(name))throw Error('hunt_search_resource_not_allowed');
  const index=api.loaded(L,name);
  if(api.type(L,index)===6){
    for(let n=1;n<=40;n++){
      const upname=api.upvalue(L,index,n);
      if(upname===null)break;
      if(upname==='require_tbl'){
        if(api.type(L,api.top(L))!==5)throw Error('constants_upvalue_not_table');
        return api.top(L);
      }
      api.settop(L,index);
    }
    throw Error('constants_upvalue_missing');
  }
  if(api.type(L,index)!==5)throw Error('constants_unavailable');
  api.get(L,index,'require_tbl');
  if(api.type(L,api.top(L))===5)return api.top(L);
  api.settop(L,index);return index;
}

function createQueuedAOIBridge(options, api) {
  const center=options.center,radius=options.radius,centers=options.centers;
  const validPoint=p=>Array.isArray(p)&&p.length===2&&p.every(v=>Number.isInteger(v)&&v>=1&&v<=1620);
  if(!Number.isSafeInteger(options.army_id)||options.army_id<=0||!validPoint(center)||
      !Number.isInteger(radius)||radius<1||radius>70||!Array.isArray(centers)||
      centers.length<1||centers.length>81||centers.some(p=>!validPoint(p)||
        (p[0]-center[0])**2+(p[1]-center[1])**2>radius**2))throw Error('hunt_search_bounds_invalid');
  if(!Number.isSafeInteger(options.session_seed)||options.session_seed<1||
      options.session_seed+centers.length-1>2147483647||
      !Number.isInteger(options.dwell_ms)||options.dwell_ms<1||options.dwell_ms>10000||
      !Number.isInteger(options.timeout_ms)||options.timeout_ms<1||options.timeout_ms>120000)
    throw Error('hunt_search_query_parameters_invalid');
  let queue=null,observer=null,started=false,closed=false,busy=false,done=null,error=null,metadata=null;
  let dispatchedSession=null;
  const dispatches=[];
  const notifications=[];
  let windowResponses=0,receivedNotifications=0;
  let deadline=null;
  function fail(reason) {
    if(closed)return;
    closed=true;
    if(queue){queue.reject(Error(String(reason)));queue=null;}
    observer=null;
    api.publish({kind:'failure',reason:String(reason),diagnostic:{dispatches,
      notifications,received_notifications:receivedNotifications,metadata}});
  }
  function guard(L) {
    api.identity(L);
    if(!api.isReady(api.readStatus(L,options.army_id)))throw Error('army_not_ready_for_search');
  }
  return {
    safeBoundary(L) {
      if(closed||busy)return;
      busy=true;
      try {
        if(error)throw error;
        if(deadline!==null&&api.now()>deadline)throw Error('hunt_search_timeout');
        if(!started){
          guard(L);
          metadata=api.install(L,(name,data)=>{
            const accepted=!closed&&observer&&data&&data.session_id===dispatchedSession;
            receivedNotifications++;
            if(notifications.length<64)notifications.push({name,session_id:data?.session_id,
              received_at:api.now(),accepted:!!accepted});
            if(accepted){windowResponses++;observer(name,data);}
          });
          started=true;
          deadline=api.now()+options.timeout_ms/1000;
          api.collect({centers:centers.map(p=>p.slice()),sessionSeed:options.session_seed,
            dwellMs:options.dwell_ms,maxQueries:81,maxEvents:10000},{
            now:api.now,sleep:async ms=>{
              await api.sleep(ms);
              if(!closed&&windowResponses===0)throw Error('hunt_search_no_session_response');
            },shouldStop:()=>closed,
            subscribe:fn=>{observer=fn;return ()=>{observer=null;};},
            sendRefresh:args=>new Promise((resolve,reject)=>{
              if(closed){reject(Error('hunt_search_cancelled'));return;}
              if(queue){reject(Error('hunt_search_parallel_request_forbidden'));return;}
              queue={args,resolve,reject};
            }),
          }).then(result=>{done=result;},reason=>{error=reason;});
        }
        if(queue){
          guard(L);
          const pending=queue;queue=null;
          try {
            dispatchedSession=pending.args.session_id;
            windowResponses=0;
            dispatches.push({...pending.args,sent_at:api.now()});
            api.sendRefresh(L,pending.args);pending.resolve();
          }
          catch(reason){pending.reject(reason);throw reason;}
        }
        if(done){
          guard(L);
          closed=true;observer=null;
          const sessions=done.sessions.map(session=>{
            const dispatch=dispatches.find(d=>d.session_id===session.session_id);
            return {...session,queued_at:session.sent_at,sent_at:dispatch.sent_at};
          });
          api.publish({kind:'complete',data:{...done,sessions,dispatches,metadata,
            transport_status:'responses_observed',notifications,received_notifications:receivedNotifications,army_id:String(options.army_id),
            center:center.slice(),radius,source:'server_aoi_refresh',observed_at:api.now()}});
        }
      }catch(reason){fail(reason.message||reason);}
      finally{busy=false;}
    },
    close() {
      closed=true;observer=null;
      if(queue){queue.reject(Error('hunt_search_cancelled'));queue=null;}
    },
  };
}

function installNativeAOISearch() {
  if(OPTIONS.operation!=='search')throw Error('hunt_search_operation_not_allowed');
  const decodeNames=new Map(),hooked=new Set();
  let ownRequest=false,sendingThread=null,foreignRequest=false,expectedRequest=null;
  let nativeCalls=0,ownEncodes=0;
  const encodedRequests=[];
  const codecStats={},unmatchedTokens={},decodedHeaders=[];
  function networkTable(L) {
    get(L,-1001000,'_LOADED');const loaded=top(L);
    get(L,loaded,'network.network');const factory=top(L);let found=false;
    for(let i=1;i<=40;i++){
      const p=upvalue(L,factory,i);if(p.isNull())break;
      if(p.readUtf8String()==='Global'){found=true;break;}
      settop(L,factory);
    }
    if(!found)throw Error('hunt_search_network_unavailable');
    get(L,-1,'network');return top(L);
  }
  function schemaType(L,core,schema,name,id) {
    get(L,core,'protocol');pushvalue(L,schema);pushstring(L,key(name));
    if(call(L,2,3,0,0,ptr(0))!==0||scalar(L,-3)!==id||type(L,-2)!==2||type(L,-1)!==0)
      throw Error('hunt_search_schema_mismatch:'+name);
    const token=pointer(L,-2).toString();settop(L,core);return token;
  }
  function infoList(L,index) {
    const info=[];pushnil(L);
    while(next(L,index)){
      if(info.length>=10000){settop(L,top(L)-2);return {truncated:true};}
      const entity=top(L),row={};
      if(type(L,entity)===5){
        for(const name of ['id','type','proto_type'])row[name]=value(L,entity,name);
        if(get(L,entity,'monster')===5){
          const m=top(L),monster={};
          for(const name of ['use_code','conf_id','end_time','monter_id','army_conf_id','troop_type'])monster[name]=value(L,m,name);
          const pos=position(L,m,'pos');if(pos)monster.pos={row:pos[0],col:pos[1]};
          row.monster=monster;
        }
        settop(L,entity);info.push(row);
      }
      settop(L,entity-1);
    }
    return info;
  }
  function readMessage(L,index,name) {
    const data={session_id:value(L,index,'session_id')};
    if(name==='sc_notify_refresh_aoi_end')data.num=value(L,index,'num');
    if(name==='sc_notify_refresh_aoi_content'){
      data.army_cnt=value(L,index,'army_cnt');
      if(get(L,index,'info_list')!==5)throw Error('hunt_search_content_invalid');
      data.info_list=infoList(L,top(L));
    }
    return data;
  }
  function metadata(L) {
    const original=top(L),result={coverage_contract_verified:false,constants:{}};
    function boundedConstants(index,depth,budget,seen) {
      const token=pointer(L,index).toString();
      if(seen.has(token))return {omitted:'repeated_table'};
      seen.add(token);
      const values=Object.create(null);pushnil(L);
      while(next(L,index)){
        if(--budget.remaining<0){settop(L,top(L)-2);values.truncated=true;break;}
        const keyType=type(L,-2),valueType=type(L,-1);
        if([3,4].includes(keyType)){
          const name=String(scalar(L,-2));
          if([3,4].includes(valueType))values[name]=scalar(L,-1);
          else if(valueType===1)values[name]=!!boolean(L,-1);
          else if(valueType===5)values[name]=depth>0?
            boundedConstants(top(L),depth-1,budget,seen):{omitted:'depth_limit'};
        }
        settop(L,top(L)-1);
      }
      return values;
    }
    // Loaded static resource definitions only; no function invocation or globals.
    for(const name of ['S1.cn.res_pro.map_attr','S1.cn.res_pro.aoi_attr','logic.map.map_aoi_processor_cfg']){
      try{
        let index;
        if(name.startsWith('S1.'))index=staticResourceTable(L,name,{
          loaded(L,moduleName){get(L,-1001000,'_LOADED');get(L,-1,moduleName);return top(L);},
          type,top,settop,get,
          upvalue(L,index,n){const p=upvalue(L,index,n);return p.isNull()?null:p.readUtf8String();},
        });
        else index=module(L,name);
        if(type(L,index)!==5)throw Error('constants_unavailable');
        result.constants[name]=boundedConstants(index,2,{remaining:1000},new Set());
      }catch(error){result.constants[name]={reason:String(error.message||error)};}
      finally{settop(L,original);}
    }
    try{
      const mgr=module(L,'logic.map.map_mgr');result.map_mode_observation={};
      for(const field of ['ignore_zoom_aoi','ignore_legacy_aoi'])result.map_mode_observation[field]=value(L,mgr,field);
      result.map_mode_observation.getters={};
      for(const name of ['is_curr_legacy_aoi','is_curr_zoom_aoi','get_zoom_aoi_level']){
        get(L,mgr,name);const index=top(L),description={lua_type:type(L,index),invoked:false,upvalues:[]};
        if(type(L,index)===6){
          for(let n=1;n<=40;n++){
            const p=upvalue(L,index,n);if(p.isNull())break;
            const upname=p.readUtf8String(),valueIndex=top(L);
            description.upvalues.push({name:upname,lua_type:type(L,valueIndex)});
            if(upname==='MapBirdviewCfg'&&type(L,valueIndex)===5&&!result.map_mode_observation.MapBirdviewCfg)
              result.map_mode_observation.MapBirdviewCfg=boundedConstants(valueIndex,2,{remaining:500},new Set());
            settop(L,index);
          }
        }
        result.map_mode_observation.getters[name]=description;
        settop(L,mgr);
      }
    }catch(error){result.map_mode_observation={reason:String(error.message||error)};}
    finally{settop(L,original);}
    return result;
  }
  function publishNative(message) {
    const diagnostic={native_calls:nativeCalls,encoded_requests:encodedRequests,
      foreign_refresh_observed:foreignRequest,codec_stats:codecStats,
      unmatched_decode_tokens:unmatchedTokens,decoded_headers:decodedHeaders,
      transport:message.diagnostic||null};
    send({kind:'search_diagnostic',data:diagnostic});
    if(foreignRequest||decodeError)lifecycle.finish({kind:'failure',
      reason:decodeError?String(decodeError.message||decodeError):'hunt_search_foreign_refresh_observed',diagnostic});
    else lifecycle.finish(message.kind==='complete'?{...message,data:{...message.data,...diagnostic}}:
      {...message,diagnostic});
  }
  const controller=createQueuedAOIBridge(OPTIONS,{
    collect:collectAOISearch,now:()=>Date.now()/1000,
    sleep:ms=>new Promise(resolve=>setTimeout(resolve,ms)),
    identity,
    readStatus(L,armyId){
      const saved=top(L);
      try{
        const mgr=module(L,'logic.player.army.my_army_mgr');get(L,mgr,'army_id_tbl');
        if(type(L,-1)!==5||rawi(L,top(L),armyId)!==5)throw Error('hunt_army_not_owned');
        return statusOnly(L,top(L));
      }finally{settop(L,saved);}
    },
    isReady,
    install(L,receive){
      const network=networkTable(L);get(L,network,'rpc');const rp=top(L);
      get(L,rp,'v_server_proto');get(L,-1,'__cobj');const server=top(L);
      get(L,rp,'v_client_proto');get(L,-1,'__cobj');const receiver=top(L);
      const core=module(L,'sproto.core');
      const requestType=schemaType(L,core,server,'cs_refresh_aoi',519);
      for(const [name,id] of [['sc_notify_refresh_aoi',719],['sc_notify_refresh_aoi_content',720],['sc_notify_refresh_aoi_end',721]])
        decodeNames.set(schemaType(L,core,receiver,name,id),name);
      for(const name of ['encode','encodep','decode','decodep','decoden']){
        get(L,core,name);const address=cfunction(L,-1);settop(L,core);
        if(address.isNull())throw Error('hunt_search_codec_unavailable');
        const encoding=name.startsWith('encode');
        const hookKey=(encoding?'encode:':'decode:')+address.toString();
        if(hooked.has(hookKey))continue;hooked.add(hookKey);
        const stats=codecStats[name]={entries:0,matched:0,return_counts:{}};
        lifecycle.attach(address,{
          onEnter(args){
            stats.entries++;
            this.searchState=args[0];this.searchName=null;
            const token=pointer(this.searchState,1).toString();
            if(encoding&&token===requestType){
              const L=this.searchState,saved=top(L),actual={};
              try{
                if(type(L,2)!==5)throw Error('hunt_search_encoded_request_invalid');
                for(const name of ['row','col','session_id'])actual[name]=value(L,2,name);
                const own=refreshEncodeMatches(actual,expectedRequest,ownRequest,
                  this.threadId===sendingThread,ownEncodes);
                encodedRequests.push({...actual,own,observed_at:Date.now()/1000});
                if(encodedRequests.length>1000)throw Error('hunt_search_encode_observation_limit');
                if(!own)foreignRequest=true;else ownEncodes++;
              }catch(error){decodeError=error;foreignRequest=true;}
              finally{settop(L,saved);}
            }
            if(!encoding){
              this.searchName=decodeNames.get(token)||null;
              if(this.searchName)stats.matched++;
              else if(Object.keys(unmatchedTokens).length<32||unmatchedTokens[token])unmatchedTokens[token]=(unmatchedTokens[token]||0)+1;
            }
          },
          onLeave(ret){
            if(!this.searchName)return;
            const L=this.searchState,saved=top(L);
            try{
              const count=ret.toInt32();stats.return_counts[count]=(stats.return_counts[count]||0)+1;
              const index=decodedReturnIndex(saved,count);
              if(!checkstack(L,128)||type(L,index)!==5)throw Error('hunt_search_decode_invalid');
              // Synchronous copying at the codec's live Lua result boundary is
              // required. Neither L nor its table indices escape this callback.
              const data=readMessage(L,index,this.searchName);
              if(decodedHeaders.length<64)decodedHeaders.push({name:this.searchName,session_id:data.session_id,received_at:Date.now()/1000});
              receive(this.searchName,data);
            }catch(error){decodeError=error;}
            finally{settop(L,saved);}
          },
        });
      }
      Interceptor.flush();
      return metadata(L);
    },
    sendRefresh(L,args){
      if(foreignRequest)throw Error('hunt_search_foreign_refresh_observed');
      const network=networkTable(L);
      const create=fn('lua_createtable','void',['pointer','int','int']);
      const set=fn('lua_setfield','void',['pointer','int','pointer']);
      get(L,network,'call');pushvalue(L,network);pushstring(L,key('cs_refresh_aoi'));
      create(L,0,3);const table=top(L);
      for(const name of ['row','col','session_id']){pushinteger(L,args[name]);set(L,table,key(name));}
      nativeCalls++;expectedRequest={...args};ownEncodes=0;
      ownRequest=true;sendingThread=Process.getCurrentThreadId();
      try{if(call(L,3,0,0,0,ptr(0))!==0)throw Error('hunt_search_query_failed');}
      finally{ownRequest=false;}
    },
    publish:publishNative,
  });
  let decodeError=null,inside=false;
  lifecycle.attach(client.getExportByName('lua_pcallk'),{onEnter(args){
    if(inside)return;inside=true;const L=args[0],saved=top(L);
    try{
      if(!checkstack(L,256))throw Error('hunt_search_stack_capacity');
      if(decodeError)throw decodeError;
      if(foreignRequest)throw Error('hunt_search_foreign_refresh_observed');
      controller.safeBoundary(L);
    }catch(error){controller.close();publishNative({kind:'failure',reason:String(error.message||error)});}
    finally{settop(L,saved);inside=false;}
  }});
  rpc.exports={close:()=>{controller.close();return lifecycle.close();},state:()=>lifecycle.state()};
}

if(typeof module==='object'&&module.exports)module.exports={createQueuedAOIBridge,refreshEncodeMatches,staticResourceTable};
if(typeof OPTIONS!=='undefined')installNativeAOISearch();
