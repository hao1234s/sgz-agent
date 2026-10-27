"""Exercise the real collector/queued native transport with offline boundaries."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def run_bridge(mode):
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node')
    assert node
    harness = r'''
const {createQueuedAOIBridge}=require(process.argv[1]);
const {collectAOISearch}=require(process.argv[2]);
const mode=process.argv[3];
let inside=false,requests=[],terminal=null,listener=null,ready=true,installs=0;
function onlyBoundary(){if(!inside)throw Error('native_operation_outside_safe_boundary');}
const ctl=createQueuedAOIBridge({army_id:1,center:[100,100],radius:70,
  centers:[[100,100],[130,100]],session_seed:11,dwell_ms:1,timeout_ms:1000},{
  collect:collectAOISearch,now:()=>Date.now()/1000,sleep:ms=>new Promise(r=>setTimeout(r,ms)),
  identity:()=>onlyBoundary(),
  readStatus:()=>{onlyBoundary();return {ready:mode==='not_ready'?false:ready};},
  isReady:row=>row.ready,
  install:(_L,receive)=>{onlyBoundary();installs++;listener=receive;return {footprint:'unknown'};},
  sendRefresh:(_L,args)=>{onlyBoundary();requests.push(args);
    if(mode!=='silent')listener('sc_notify_refresh_aoi_content',{session_id:args.session_id,info_list:[]});
    if(mode==='phase_change')ready=false;},
  publish:value=>{terminal=value;},
});
(async()=>{
 for(let i=0;i<1000&&!terminal;i++){
   if(mode==='early'&&requests.length===1){
     await new Promise(r=>setTimeout(r,5));
     listener('sc_notify_refresh_aoi_content',{session_id:12,info_list:[]});
   }
   inside=true;try{ctl.safeBoundary('L');}finally{inside=false;}
   await new Promise(r=>setTimeout(r,1));
 }
 ctl.close();
 console.log(JSON.stringify({requests,terminal,installs}));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    result = subprocess.run([node, '-e', harness,
                             str(root/'src/sgz_agent/hunt_search_bridge.js'),
                             str(root/'src/sgz_agent/hunt_search.js'), mode],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_native_search_queries_are_serial_and_only_sent_at_safe_boundaries():
    result = run_bridge('ready')
    assert result['terminal']['kind'] == 'complete'
    data = result['terminal']['data']
    assert data['complete'] is False
    assert data['coverage_reason'] == 'server_region_coverage_unverified'
    assert len(data['events']) == 2
    assert result['requests'] == [
        {'row': 100, 'col': 100, 'session_id': 11},
        {'row': 130, 'col': 100, 'session_id': 12},
    ]
    assert result['installs'] == 1


@pytest.mark.parametrize(('mode', 'sent'), [('not_ready', 0), ('phase_change', 1)])
def test_readonly_status_guard_blocks_nonready_armies_before_each_query(mode, sent):
    result = run_bridge(mode)
    assert len(result['requests']) == sent
    assert result['terminal']['kind'] == 'failure'
    assert 'army_not_ready_for_search' in result['terminal']['reason']


def test_session_matching_data_before_native_dispatch_is_rejected():
    result = run_bridge('early')
    assert result['terminal']['kind'] == 'complete'
    assert len(result['terminal']['data']['events']) == 2
    assert len(result['terminal']['data']['dispatches']) == 2


def test_refresh_encoder_requires_exact_single_own_call_and_rejects_external_traffic():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node')
    harness = r'''
const {refreshEncodeMatches}=require(process.argv[1]);
const expected={row:100,col:100,session_id:11};
console.log(JSON.stringify([
  refreshEncodeMatches(expected,expected,true,true,0),
  refreshEncodeMatches(expected,expected,false,true,0),
  refreshEncodeMatches(expected,expected,true,false,0),
  refreshEncodeMatches({...expected,session_id:12},expected,true,true,0),
  refreshEncodeMatches({...expected,row:101},expected,true,true,0),
  refreshEncodeMatches(expected,expected,true,true,1),
]));
'''
    output = subprocess.run([node, '-e', harness,
                             str(root/'src/sgz_agent/hunt_search_bridge.js')],
                            capture_output=True, text=True, timeout=10)
    assert output.returncode == 0, output.stderr
    assert json.loads(output.stdout) == [True, False, False, False, False, False]


def test_no_response_stops_after_first_query_and_reports_transport_failure():
    result = run_bridge('silent')
    assert len(result['requests']) == 1
    assert result['terminal']['kind'] == 'failure'
    assert result['terminal']['reason'] == 'hunt_search_no_session_response'
    assert result['terminal']['diagnostic']['dispatches'][0]['session_id'] == 11


def test_static_resource_function_reads_named_upvalue_without_calling_it():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which('node')
    harness = r'''
const {staticResourceTable}=require(process.argv[1]);
const visited=[];
const index=staticResourceTable('L','S1.cn.res_pro.map_attr',{
  loaded:()=>1,type:(_L,i)=>i===1?6:5,top:()=>2,settop:()=>{},
  upvalue:(_L,i,n)=>{visited.push(n);return n===1?'other':n===2?'require_tbl':null;},
  get:()=>{throw Error('must_not_getfield_on_function');},
});
console.log(JSON.stringify({index,visited}));
'''
    output = subprocess.run([node, '-e', harness,
                             str(root/'src/sgz_agent/hunt_search_bridge.js')],
                            capture_output=True, text=True, timeout=10)
    assert output.returncode == 0, output.stderr
    assert json.loads(output.stdout) == {'index': 2, 'visited': [1, 2]}
