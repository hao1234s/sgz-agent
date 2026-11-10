// Read-only 519 collection orchestration. No native attachment or network API.
// The identity-verified bridge supplies sendRefresh({row,col,session_id}) and a
// schema-identified observer. No count or timeout is treated as coverage proof.
async function collectAOISearch(options, bridge) {
  const {centers,sessionSeed,dwellMs=250,maxQueries=81,maxEvents=10000}=options;
  if(!Array.isArray(centers)||centers.length<1||centers.length>maxQueries||
      !Number.isSafeInteger(maxQueries)||maxQueries<1||maxQueries>1000||
      !Number.isSafeInteger(sessionSeed)||sessionSeed<1||
      !Number.isSafeInteger(sessionSeed+centers.length)||
      !Number.isSafeInteger(dwellMs)||dwellMs<1||dwellMs>10000||
      !Number.isSafeInteger(maxEvents)||maxEvents<1||maxEvents>100000)
    throw Error('hunt_search_query_parameters_invalid');
  for(const center of centers)if(!Array.isArray(center)||center.length!==2||
      center.some(v=>!Number.isInteger(v)||v<1||v>1620))throw Error('hunt_search_bounds_invalid');
  const {sendRefresh,subscribe,sleep,now,shouldStop=()=>false}=bridge;
  if([sendRefresh,subscribe,sleep,now].some(fn=>typeof fn!=='function'))throw Error('hunt_search_bridge_invalid');
  const events=[],sessions=[];
  let active=null,overflow=false,cancelled=false;
  const unsubscribe=subscribe((name,data)=>{
    if(!active||!data||data.session_id!==active.session_id||
        !['sc_notify_refresh_aoi','sc_notify_refresh_aoi_content','sc_notify_refresh_aoi_end'].includes(name))return;
    const timestamp=now();
    if(!Number.isFinite(timestamp)||timestamp<active.sent_at)return;
    if(events.length>=maxEvents){overflow=true;return;}
    // Copy immediately: Lua decoder tables are reused after the callback.
    events.push({name,data:JSON.parse(JSON.stringify(data)),received_at:timestamp});
  });
  if(typeof unsubscribe!=='function')throw Error('hunt_search_unsubscribe_required');
  try {
    for(let i=0;i<centers.length;i++){
      if(shouldStop()){cancelled=true;break;}
      const sentAt=now();
      if(!Number.isFinite(sentAt)||sentAt<=0)throw Error('hunt_search_time_invalid');
      active={session_id:sessionSeed+i,center:centers[i].slice(),sent_at:sentAt};
      sessions.push(active);
      await sendRefresh({row:centers[i][0],col:centers[i][1],session_id:active.session_id});
      await sleep(dwellMs);
      active.finished_at=now();
      if(!Number.isFinite(active.finished_at)||active.finished_at<sentAt)throw Error('hunt_search_time_reversed');
      active.reason='bounded_collection_window_coverage_unknown';
      active=null;
      if(overflow)break;
    }
    return {complete:false,coverage_reason:cancelled?'search_cancelled':
      overflow?'aoi_observation_limit':'server_region_coverage_unverified',
      events,sessions,overflow,cancelled};
  } finally {
    active=null;
    unsubscribe();
  }
}

if(typeof module!=='undefined')module.exports={collectAOISearch};
