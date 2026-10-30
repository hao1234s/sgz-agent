"""A sticky tying flag cannot override complete current own-city idle proof."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess


SOURCE=Path(__file__).parents[1]/'src/sgz_agent/client/replenish_client.js'


def idle_status():
    return {'army_id':347988,'army_type':1,'city_id':65364,'dispatch_build_id':65364,
        'status_codes':[1],'army_status':1,'system_status':1,'is_tying':True,
        'movement_end_time':0,'movement_total_time':0,'at_dispatch':True,
        'position':[1440,1236],'dispatch_position':[1440,1236],
        'command':None,'destination':None,'enemy_army_id':None,'fight_position':None}


def run_node(code,payload):
    result=subprocess.run(['node','-e',code,str(SOURCE),json.dumps(payload)],
                          text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    return json.loads(result.stdout)


def test_full_current_own_city_idle_proof_tolerates_sticky_tying_but_rejects_every_missing_guard():
    good=idle_status();cases=[good,{**good,'is_tying':False},{**good,'command':0,'enemy_army_id':0}]
    required=['army_id','army_type','city_id','dispatch_build_id','status_codes','army_status','system_status',
              'movement_end_time','movement_total_time','at_dispatch','position','dispatch_position',
              'command','destination','enemy_army_id','fight_position']
    for key in required:
        row=deepcopy(good);row.pop(key);cases.append(row)
    for patch in [{'army_type':2},{'army_id':True},{'city_id':0},{'dispatch_build_id':11183},
            {'status_codes':[2]},{'status_codes':[1,24]},{'army_status':24},{'system_status':25},
            {'movement_end_time':100},{'movement_total_time':1},{'at_dispatch':False},
            {'position':[1441,1236]},{'position':[0,1236],'dispatch_position':[0,1236]},
            {'position':[1440.5,1236],'dispatch_position':[1440.5,1236]},
            {'command':12},{'destination':[1440,1236]},{'enemy_army_id':8},{'fight_position':[1440,1236]}]:
        cases.append({**good,**patch})
    code=r'''
const fs=require('node:fs'),vm=require('node:vm'),c={};vm.createContext(c);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').split('// Native adapter')[0],c);
if(typeof c.ownCityIdleStatus!=='function')throw Error('own-city idle status guard is not implemented');
console.log(JSON.stringify(JSON.parse(process.argv[2]).map(row=>c.ownCityIdleStatus(row))));
'''
    assert run_node(code,cases)==[True,True,True]+[False]*(len(cases)-3)


def test_refill_snapshot_uses_full_idle_guard_before_ownership_or_hero_reads():
    good=idle_status();cases=[good,{**good,'status_codes':[24]},
        {**good,'system_status':24},{**good,'command':12},{**good,'enemy_army_id':8}]
    code=r'''
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8').split('let busy=false,finished=false')[0];
const results=[];
for(const raw of JSON.parse(process.argv[2])){
 const calls=[];
 const c={top:()=>1,settop:()=>{},get:()=>5,type:()=>5,rawi:()=>5,
  statusOnly:()=>raw,readyArmy:()=>{throw Error('unexpected_hero_read')},
  module:(_L,name)=>{calls.push(name);if(name==='logic.player.player_mgr')throw Error('ownership_gate_reached');return 1;}};
 vm.createContext(c);vm.runInContext(source,c);
 try{results.push({phase:c.refillSnapshot({},347988).phase,calls});}
 catch(error){results.push({error:error.message,calls});}
}
console.log(JSON.stringify(results));
'''
    rows=run_node(code,cases)
    assert rows[0]['error']=='ownership_gate_reached'
    assert rows[0]['calls']==['logic.player.army.my_army_mgr','logic.player.player_mgr']
    for row in rows[1:]:
        assert row=={'phase':'unavailable','calls':['logic.player.army.my_army_mgr']}
