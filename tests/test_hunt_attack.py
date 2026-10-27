"""Controlled attack contract: no arbitrary command and no ambiguous replay."""
import importlib
import json
from pathlib import Path
import subprocess

import pytest


def attack_module():
    from sgz_agent import hunt_attack
    return hunt_attack


def plan():
    return {'world':'w', 'army_id':'364067', 'position':[558,1527],
            'base_id':'11183', 'created_at':100, 'expires_at':120,
            'target':{'id':'2511112','x':587,'y':1550,'use_code':1,'conf_id':10,
                      'observed_at':100,'end_time':1000},
            'heroes':[{'id':str(i),'troops':10000,'capacity':10000} for i in [1,2,3]],
            'min_troops':20000,'min_morale':100,'stamina_cost':15,
            'preview':{'path_len':30,'need_time':300,'need_energy':15,'result':1},
            'morale_cost_per_cell':0.6,'arrival_morale':102,
            'defenders_verified':True,'orders_available':1}


def snapshot():
    return {'army':{'army_id':364067,'army_type':1,'is_tying':False,'status_codes':[19],
                    'position':[558,1527],'dispatch_position':[558,1527],
                    'dispatch_build_id':11183,'dispatch_entered_at':50,'observed_at':101,
                    'movement_end_time':0,'movement_total_time':0},
            'heroes':[{'id':str(i),'troops':10000,'capacity':10000,'stamina':120,'wounded_until':0}
                      for i in [1,2,3]],'morale':120,'stamina':120,'orders_available':1,
            'cost_decree':1,'defender_count':3,'morale_cost_per_cell':0.6}


def test_request_matches_observed_ui_and_always_auto_returns():
    m=attack_module()
    q=m.attack_request(plan())
    assert q=={'cmd':12,'army_id':364067,'target_pos':{'row':587,'col':1550},
               'is_auto_fallback':True,'path_pos':[],'cmd_target_type':1,'is_occupy':False,
               'ext_str':'','extra_data':{'interrupt_guard':True,'cancel_conscription':True}}
    for patch in ({'use_code':3},{'conf_id':9},{'x':0},{'id':'invalid'}):
        p=plan();p['target'].update(patch)
        with pytest.raises(ValueError):m.attack_request(p)


def test_javascript_send_guard_blocks_stale_or_changed_preflight():
    m=attack_module()
    source=Path(m.__file__).with_suffix('.js').read_text(encoding='utf-8')
    guard=source.split('// Native transport')[0]
    cases=[{'plan':plan(),'row':snapshot(),'target':plan()['target'],'now':101}]
    for category, patch in [('plan',{'expires_at':100}),('plan',{'orders_available':0}),
                            ('plan',{'arrival_morale':97}),('plan',{'defenders_verified':False}),
                            ('row',{'morale':110}),('row',{'stamina':10}),('row',{'orders_available':0}),
                            ('target',{'id':'999'}),('target',{'use_code':3}),
                            ('target',{'end_time':100})]:
        case=json.loads(json.dumps(cases[0]));case[category].update(patch);cases.append(case)
    for patch in [{'status_codes':[2]}, {'is_tying':True}, {'dispatch_build_id':55},
                  {'position':[560,1527]}]:
        case=json.loads(json.dumps(cases[0]));case['row']['army'].update(patch);cases.append(case)
    case=json.loads(json.dumps(cases[0]));case['row']['heroes'][0]['troops']=1;cases.append(case)
    output=subprocess.run(['node','-e',guard+'\nconsole.log(JSON.stringify('+json.dumps(cases)+
                           '.map(x=>validateAttack(x.plan,x.row,x.target,x.now))));'],
                          capture_output=True,text=True,check=True)
    results=json.loads(output.stdout)
    assert results[0] is None
    assert all(isinstance(r,str) and r for r in results[1:])


def test_untagged_ack_cannot_release_fence_and_departure_needs_matching_state():
    m=attack_module();q=m.attack_request(plan())
    def event(name,data,n):return {'name':name,'data':data,'sequence':n}
    sent=event('cs_army_cmd',q,1)
    assert m.command_receipt(q,[sent,event('cs_army_cmd.response',{'result':False},2)])['status']=='unknown'
    assert m.command_receipt(q,[sent,event('cs_army_cmd.response',{'result':True},2)])['status']=='unknown'
    assert m.command_receipt(q,[sent])['status']=='unknown'
    for value in (1,'true',None):
        assert m.command_receipt(q,[sent,event('cs_army_cmd.response',{'result':value},2)])['status']=='unknown'
    # A second command makes untagged acknowledgements ambiguous.
    assert m.command_receipt(q,[sent,event('cs_army_cmd',q,2),event('cs_army_cmd.response',{'result':True},3)])['status']=='unknown'
    assert m.command_receipt(q,[event('cs_army_cmd.response',{'result':True},1),sent])['status']=='unknown'
    departure=event('sc_sync_army_status',{'army_info':{'army_id':364067,'status':{
        'cmd':12,'status':[2],'end_pos':q['target_pos'],'is_auto_fallback':True,
        'total_time':300,'end_time':500}}},3)
    assert m.command_receipt(q,[sent,departure])['status']=='accepted'
    departure['data']['army_info']['army_id']=364068
    assert m.command_receipt(q,[sent,departure])['status']=='unknown'


def test_transport_claim_survives_restart_and_cannot_replay(tmp_path):
    m=attack_module()
    with m.AttackJournal(tmp_path) as store:
        record=store.prepare('request','w',plan())
        assert store.claim(record['id']) is True
    with m.AttackJournal(tmp_path) as reopened:
        assert reopened.claim(record['id']) is False
        assert reopened.prepare('request','w',plan())['id']==record['id']
        with pytest.raises(RuntimeError,match='pending'):
            reopened.prepare('new-request','w',plan())
        reopened.finish(record['id'],{'status':'unknown','sent':True})
        with pytest.raises(RuntimeError,match='pending'):
            reopened.prepare('third-request','w',plan())
        changed=plan();changed['target']['id']='2511113'
        with pytest.raises(ValueError,match='conflict'):
            reopened.prepare('request','w',changed)


def test_journal_target_claim_blocks_another_army_and_rejection_releases(tmp_path):
    m=attack_module()
    with m.AttackJournal(tmp_path) as store:
        record=store.prepare('request','w',plan())
        other=plan();other['army_id']='364068'
        with pytest.raises(RuntimeError,match='target'):
            store.prepare('other','w',other)
        store.claim(record['id'])
        with pytest.raises(ValueError):store.finish(record['id'],{'status':'rejected','sent':True})
        store.finish(record['id'],{'status':'rejected','sent':False})
        assert store.prepare('other','w',other)['plan']==other


def test_controlled_runtime_requires_explicit_test_mode_and_cannot_change_plan(tmp_path):
    from types import SimpleNamespace
    m=attack_module()
    config=SimpleNamespace(data_dir=tmp_path,identity={'world':'w','role_id':'1','region':'r',
        'api_server_id':'s','api_season':1},executable=Path('S3Client.exe'))
    with pytest.raises(ValueError,match='controlled'):
        m.ControlledAttackRuntime(config,plan=plan())
    p=plan()
    runtime=m.ControlledAttackRuntime(config,plan=p,controlled_verification=True)
    p['army_id']='999'
    source=runtime._source()
    assert 'function runtimeAttack(' in source
    options=json.loads(source.split('\n')[0].removeprefix('const OPTIONS = ').removesuffix(';'))
    assert options['controlled_attack_plan']['army_id']=='364067'
    assert runtime._arguments('attack',{})=={}
    with pytest.raises(ValueError):runtime._arguments('attack',{'cmd':12})


def test_native_executor_claims_before_send_and_never_retries_a_throw():
    m=attack_module()
    source=Path(m.__file__).with_suffix('.js').read_text(encoding='utf-8').split('// Native transport')[0]
    harness='''
const assert=require('node:assert/strict');
let calls=0;
const api={snapshot:()=>ROW,target:()=>PLAN.target,send:()=>{calls++;throw Error('connection_lost');}};
const executor=createAttackExecutor(PLAN,api);
assert.equal(executor.run(101).status,'unknown');
assert.equal(executor.run(102).status,'unknown');
assert.equal(calls,1);
const blocked=createAttackExecutor({...PLAN,expires_at:100},api);
assert.deepEqual(blocked.run(101),{status:'rejected',sent:false,reason:'attack_plan_stale'});
assert.equal(calls,1);
'''
    subprocess.run(['node','-e',source+'\nconst PLAN='+json.dumps(plan())+';const ROW='+
                    json.dumps(snapshot())+';\n'+harness],check=True,capture_output=True,text=True)


def test_morale_override_is_world_bound_and_never_negative(tmp_path):
    from types import SimpleNamespace
    m=attack_module();config=SimpleNamespace(data_dir=tmp_path,identity={'world':'w'})
    assert m.morale_floor(config)==100
    path=tmp_path/'hunt-user-policy.json'
    for value,expected in [({'world':'w','min_arrival_morale':0,'authorization':'user'},0),
                           ({'world':'other','min_arrival_morale':0,'authorization':'user'},100)]:
        path.write_text(json.dumps(value));assert m.morale_floor(config)==expected
    for invalid in (-1,True,float('nan')):
        path.write_text(json.dumps({'world':'w','min_arrival_morale':invalid,'authorization':'user'}))
        with pytest.raises(ValueError):m.morale_floor(config)


def test_controlled_entrypoint_reuses_receipt_without_second_native_call(tmp_path):
    from types import SimpleNamespace
    m=attack_module();calls=[]
    config=SimpleNamespace(data_dir=tmp_path,identity={'world':'w'})
    class Runtime:
        def __init__(self,*args,**kwargs):
            calls.append('created');self.generation='g'
            self.events=SimpleNamespace(events=lambda **kw:[
                {'sequence':1,'event':{'name':'cs_army_cmd','data':m.attack_request(plan())}},
                {'sequence':2,'event':{'name':'cs_army_cmd.response','data':{'result':False}}}])
        def start(self):calls.append('start')
        def request(self,operation):calls.append(operation);return {'status':'submitted','sent':True}
        def close(self):calls.append('close')
        def state(self):return {'phase':'closed','fault':None,'cleanup_fault':None,'bridge_retained':False}
    first=m.execute_controlled(config,request_id='request',plan=plan(),runtime_factory=Runtime)
    second=m.execute_controlled(config,request_id='request',plan=plan(),runtime_factory=Runtime)
    assert first['status']==second['status']=='unknown'
    assert calls==['created','start','attack','close']


def test_reconcile_requires_fresh_matching_postsend_movement_and_keeps_fence(tmp_path):
    m=attack_module()
    with m.AttackJournal(tmp_path) as store:
        record=store.prepare('request','w',plan());store.claim(record['id'])
        store.finish(record['id'],{'status':'unknown','sent':True})
        record=store.get(record['id']);start=record['claimed_at']
        row={'army_id':364067,'status_codes':[2],'command':12,'destination':[587,1550],
             'auto_return':True,'movement_total_time':300,'movement_end_time':start+303,
             'position':[559,1527],'observed_at':start+10}
        bad={**row,'destination':[588,1550]}
        with pytest.raises(ValueError):store.reconcile_departure(record['id'],bad,now=start+11)
        with pytest.raises(ValueError):store.reconcile_departure(record['id'],row,now=start+50)
        receipt=store.reconcile_departure(record['id'],row,now=start+11)
        assert receipt['status']=='accepted' and receipt['departure_verified'] is True
        assert receipt['encounter_complete'] is False
        assert len(store.pending('w'))==1


def test_three_wins_require_exact_target_team_and_return_and_do_not_unfence(tmp_path):
    from copy import deepcopy
    m=attack_module()
    with m.AttackJournal(tmp_path) as store:
        record=store.prepare('request','w',plan());store.claim(record['id'])
        store.finish(record['id'],{'status':'accepted','sent':True,'departure_verified':True})
        record=store.get(record['id']);t=record['claimed_at']+300
        proof={'entity_id':'2511112','monster_id':123,'x':587,'y':1550,
               'use_code':1,'conf_id':10,'observed_at':100}
        reports=[{'id':i,'group_id':'123','pos':[587,1550],'player_id':42,'result':1,
                  'hero_ids':[1,2,3],'primary_hero_id':1,'battle_time':t} for i in [1001,1002,1003]]
        post={'army_id':364067,'status_codes':[19],'position':[558,1527],
              'dispatch_build_id':11183,'movement_total_time':0,'movement_end_time':0,
              'observed_at':t+10,'is_tying':True}
        for rs in [reports[:1],reports[:2],reports+[reports[0]],
                   [{**r,'group_id':'456'} for r in reports],
                   [{**r,'hero_ids':[4,5,6]} for r in reports],
                   [{**r,'player_id':43} for r in reports],
                   [{**r,'result':2} for r in reports]]:
            with pytest.raises(ValueError):
                store.record_victory(record['id'],target_witness=proof,reports=rs,
                                     role_id=42,post_status=post,now=t+11)
        with pytest.raises(ValueError):
            store.record_victory(record['id'],target_witness=proof,reports=reports,
                                 role_id=42,post_status={**post,'status_codes':[25]},now=t+11)
        result=store.record_victory(record['id'],target_witness=proof,reports=reports,
                                   role_id=42,post_status=post,now=t+11)
        assert result['encounter_complete'] is True and result['result']=='victory'
        assert result['completed_waves']==3 and len(store.pending('w'))==1
