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
    output=subprocess.run(['node','-'],input=guard+'\nconsole.log(JSON.stringify('+json.dumps(cases)+
                           '.map(x=>validateAttack(x.plan,x.row,x.target,x.now))));',
                          capture_output=True,text=True,check=True)
    results=json.loads(output.stdout)
    assert results[0] is None
    assert all(isinstance(r,str) and r for r in results[1:])


def test_attack_accepts_lingering_flag_only_with_two_consistent_idle_getters():
    source=Path(attack_module().__file__).with_suffix('.js').read_text(encoding='utf-8').split('// Native transport')[0]
    row=snapshot();row['army'].update(is_tying=True,army_status=19,system_status=19)
    program=source+';const p='+json.dumps(plan())+';const r='+json.dumps(row)+''';
console.log(JSON.stringify([validateAttack(p,r,p.target,101),
validateAttack(p,{...r,army:{...r.army,system_status:25}},p.target,101)]));'''
    result=subprocess.run(['node','-e',program],check=True,capture_output=True,text=True)
    a,b=json.loads(result.stdout);assert a is None and isinstance(b,str)


def test_field_send_revalidates_stationary_origin_and_unchanged_owned_dispatch_reference():
    from copy import deepcopy
    source=Path(attack_module().__file__).with_suffix('.js').read_text(encoding='utf-8').split('// Native transport')[0]
    p=plan();p.update(launch_mode='stationary_field',dispatch_position=[560,1528],
        return_base_id='11183',auto_return_mode='game_managed_auto_return',return_destination_verified=False,
        return_base_witness={'world':'w','army_id':'364067','id':'11183','position':[560,1528],
                             'source':'client_lua_owned_build','owned':True,'observed_at':100})
    row=snapshot();row['army'].update(status_codes=[3],army_status=3,system_status=3,
        dispatch_position=[560,1528],at_dispatch=False,command=None,enemy_army_id=None,
        fight_position=None,destination=None)
    valid={'plan':p,'row':row,'target':p['target'],'now':101};cases=[valid]
    for part,patch in [('army',{'status_codes':[25]}),('army',{'army_status':25}),
        ('army',{'dispatch_position':[561,1528]}),('army',{'dispatch_build_id':99}),
        ('army',{'position':[559,1527]}),('army',{'movement_total_time':1}),
        ('army',{'at_dispatch':True}),('army',{'command':12}),
        ('army',{'enemy_army_id':100}),('army',{'fight_position':[587,1550]}),
        ('plan',{'return_base_id':'99'}),('plan',{'auto_return_mode':'invented_endpoint'}),
        ('plan',{'return_destination_verified':True}),('base',{'owned':False}),
        ('base',{'position':[561,1528]}),('base',{'world':'other'})]:
        case=deepcopy(valid)
        selected=case['row']['army'] if part=='army' else case['plan']['return_base_witness'] if part=='base' else case['plan']
        selected.update(patch);cases.append(case)
    result=subprocess.run(['node','-'],input=source+';console.log(JSON.stringify('+json.dumps(cases)+
        '.map(x=>validateAttack(x.plan,x.row,x.target,x.now))));',check=True,capture_output=True,text=True)
    actual=json.loads(result.stdout)
    assert actual[0] is None
    assert all(isinstance(r,str) and r for r in actual[1:])


def test_javascript_route_accounting_excludes_verified_free_cells_but_keeps_time():
    source=Path(attack_module().__file__).with_suffix('.js').read_text(encoding='utf-8').split('// Native transport')[0]
    program=source+''';
const segment=(kind,length,seconds,cost,verified)=>({kind,actual_path_len:length,seconds,
  path_verified:true,path_source:'server_route_preview.path',morale_cost_per_cell:cost,
  morale_rule_verified:verified,morale_rule_source:'observed_current_rules'});
const relocation=segment('relocate',80,400,0,true), attack=segment('attack',20,300,0.6,false);
const valid=accountRoute([relocation,attack],120);
let rejected=false;
try {accountRoute([{...relocation,morale_rule_verified:false},attack],120);}
catch(error){rejected=true;}
console.log(JSON.stringify({valid,rejected}));'''
    result=subprocess.run(['node','-'],input=program,check=True,capture_output=True,text=True)
    actual=json.loads(result.stdout)
    assert actual['valid']['total_seconds']==700
    assert actual['valid']['morale_charged_path_cells']==20
    assert actual['valid']['estimated_arrival_morale']==108
    assert actual['valid']['arrival_morale_verified'] is False
    assert actual['rejected'] is True


def test_send_guard_keeps_search_radius_separate_from_server_route_length():
    from copy import deepcopy
    source=Path(attack_module().__file__).with_suffix('.js').read_text(encoding='utf-8').split('// Native transport')[0]
    p=plan();p.update(search_radius=140,min_morale=0,arrival_morale=48)
    p['target'].update(x=658,y=1527)
    p['preview']['path_len']=120
    cases=[{'plan':p,'row':snapshot(),'target':p['target'],'now':101}]
    for radius in (70,141):
        case=deepcopy(cases[0]);case['plan']['search_radius']=radius;cases.append(case)
    result=subprocess.run(['node','-'],input=source+';console.log(JSON.stringify('+json.dumps(cases)+
        '.map(x=>validateAttack(x.plan,x.row,x.target,x.now))));',check=True,capture_output=True,text=True)
    actual=json.loads(result.stdout)
    assert actual[0] is None
    assert all(isinstance(r,str) and r for r in actual[1:])


def test_send_guard_rederives_native_path_cells_and_rejects_forged_route_accounting():
    from copy import deepcopy
    source=Path(attack_module().__file__).with_suffix('.js').read_text(encoding='utf-8').split('// Native transport')[0]
    p=plan();p.update(position=[700,1522],search_radius=140,min_morale=78,arrival_morale=78,
        preflight_morale=120,morale_charged_path_cells=70,
        route_accounting={'total_seconds':700,'total_actual_path_cells':70,'morale_charged_path_cells':70,
            'morale_free_path_cells':0,'estimated_morale_cost':42,'estimated_arrival_morale':78,
            'arrival_morale_source':'segment_path_cell_estimate','arrival_morale_verified':False,
            'segments':[{'kind':'attack','actual_path_len':70,'seconds':700,'path_verified':True,
                'path_source':'cs_army_preview.path_nodes_minus_origin','morale_cost_per_cell':0.6,
                'morale_rule_verified':False,'morale_rule_source':'own_army.status.cur_shiqi_cost_percell'}]})
    p['target'].update(x=630,y=1526)
    p['preview'].update(path_len=71,need_time=700,actual_path_len=70,path_length_verified=True,
        path_length_source='cs_army_preview.path_nodes_minus_origin',
        path_waypoints=[[700,1522],[679,1511],[641,1530],[631,1525],[630,1526]])
    row=snapshot();row['army'].update(position=[700,1522],dispatch_position=[700,1522])
    valid={'plan':p,'row':row,'target':p['target'],'now':101};cases=[valid]
    for patch in [dict(actual_path_len=69),dict(path_length_verified=False),
                  dict(path_length_source='coordinate_distance'),dict(path_waypoints=[[700,1522],[631,1526]])]:
        case=deepcopy(valid);case['plan']['preview'].update(patch);cases.append(case)
    for patch in [dict(morale_free_path_cells=70),dict(estimated_morale_cost=0),dict(total_seconds=0)]:
        case=deepcopy(valid);case['plan']['route_accounting'].update(patch);cases.append(case)
    for patch in [dict(route_accounting=None),dict(morale_charged_path_cells=None),
                  dict(route_accounting=None,morale_charged_path_cells=None)]:
        case=deepcopy(valid);case['plan'].update(patch);cases.append(case)
    for nodes in (1,2,70):
        case=deepcopy(valid)
        case['plan'].update(route_accounting=None,morale_charged_path_cells=None,arrival_morale=120)
        case['plan']['preview'].update(path_len=nodes,actual_path_len=nodes-1)
        cases.append(case)
    result=subprocess.run(['node','-'],input=source+';console.log(JSON.stringify('+json.dumps(cases)+
        '.map(x=>validateAttack(x.plan,x.row,x.target,x.now))));',check=True,capture_output=True,text=True)
    actual=json.loads(result.stdout)
    assert actual[0] is None
    assert all(isinstance(r,str) and r for r in actual[1:])


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


def test_managed_stop_before_claim_is_unsent_without_preparing_or_entering_native(tmp_path):
    from threading import Event
    from types import SimpleNamespace
    m=attack_module();stop=Event();stop.set()
    config=SimpleNamespace(data_dir=tmp_path,identity={'world':'w'})
    def native(*args,**kwargs):pytest.fail('stopped operation entered native runtime')
    receipt=m.execute_under_lease(config,request_id='stopped',plan=plan(),runtime_factory=native,stop_event=stop)
    assert receipt['status']=='rejected' and receipt['sent'] is False
    with m.AttackJournal(tmp_path) as journal:
        assert journal.db.execute('SELECT count(*) FROM attacks').fetchone()[0]==0


def test_managed_stop_after_claim_preserves_unknown_and_prevents_native_retry(tmp_path,monkeypatch):
    from threading import Event
    from types import SimpleNamespace
    m=attack_module();stop=Event();config=SimpleNamespace(data_dir=tmp_path,identity={'world':'w'})
    claim=m.AttackJournal.claim
    def stop_after_claim(journal,identity):
        result=claim(journal,identity);stop.set();return result
    monkeypatch.setattr(m.AttackJournal,'claim',stop_after_claim)
    def native(*args,**kwargs):pytest.fail('stopped claimed operation entered native runtime')
    receipt=m.execute_under_lease(config,request_id='claimed',plan=plan(),runtime_factory=native,stop_event=stop)
    assert receipt['status']=='unknown' and receipt['sent'] is None
    assert m.execute_under_lease(config,request_id='claimed',plan=plan(),runtime_factory=native,stop_event=stop)==receipt
    with m.AttackJournal(tmp_path) as journal:
        assert len(journal.pending('w'))==1


@pytest.mark.parametrize('stop_at',['start','submitted'])
def test_managed_runtime_receives_service_stop_and_never_sends_after_stopped_start(tmp_path,stop_at):
    from threading import Event
    from types import SimpleNamespace
    m=attack_module();stop=Event();calls=[]
    config=SimpleNamespace(data_dir=tmp_path,identity={'world':'w'})
    class Runtime:
        def __init__(self,*args,stop_event,**kwargs):
            assert stop_event is stop
            self.generation='stop-test';self.events=SimpleNamespace(events=lambda **kw:[])
        def start(self):
            calls.append('start')
            if stop_at=='start':stop.set()
        def request(self,operation):
            assert not stop.is_set();calls.append(operation);stop.set()
            return {'status':'submitted','sent':True}
        def close(self):calls.append('close')
        def state(self):return {'phase':'closed','fault':None,'cleanup_fault':None,'bridge_retained':False}
    receipt=m.execute_under_lease(config,request_id='runtime-stop',plan=plan(),runtime_factory=Runtime,stop_event=stop)
    assert receipt['status']=='unknown'
    assert receipt['sent'] is (True if stop_at=='submitted' else None)
    assert calls==(['start','attack','close'] if stop_at=='submitted' else ['start','close'])


def missed_departure_run(tmp_path,monkeypatch,*,patch=None,read_error=False,final_patch=None):
    from itertools import count
    from types import SimpleNamespace
    m=attack_module();clock={'now':100};calls=[];ticks=count(0,16)
    monkeypatch.setattr(m.time,'time',lambda:clock['now'])
    monkeypatch.setattr(m.time,'monotonic',lambda:next(ticks))
    config=SimpleNamespace(data_dir=tmp_path,identity={'world':'w'})
    row={'army_id':364067,'status_codes':[2],'command':12,'destination':[587,1550],
         'auto_return':True,'movement_total_time':300,'movement_end_time':401,
         'position':[559,1527],'observed_at':116,**(patch or {})}
    class Runtime:
        def __init__(self,*args,**kwargs):
            self.generation='departure-read';self.phase='running'
            self.events=SimpleNamespace(events=lambda **kw:[])
        def start(self):calls.append('start')
        def request(self,operation):
            calls.append(operation)
            if operation=='attack':return {'status':'submitted','sent':True}
            assert operation=='roster','departure check must never read heroes or troops'
            clock['now']=116
            if read_error:raise RuntimeError('status_read_failed')
            return {'complete':True,'armies':[row],'observed_at':116}
        def roster_status(self):return self.request('roster')
        def close(self):calls.append('close');self.phase='closed'
        def state(self):
            return {'generation':self.generation,'phase':self.phase,'fault':None,
                    'cleanup_fault':None,'bridge_retained':False,
                    **(final_patch or {} if self.phase=='closed' else {})}
    result=m.execute_under_lease(config,request_id='missed-departure',plan=plan(),runtime_factory=Runtime)
    return result,calls,m


def test_missed_packet_reconciles_only_after_fresh_status_read_and_clean_close(tmp_path,monkeypatch):
    result,calls,m=missed_departure_run(tmp_path,monkeypatch)
    assert calls==['start','attack','roster','close']
    assert result['status']=='accepted' and result['sent'] is True
    assert result['departure_verified'] is True and result['encounter_complete'] is False
    assert result['departure_status_read']['armies']==[result['departure_snapshot']]
    assert 'heroes' not in result['departure_snapshot']
    with m.AttackJournal(tmp_path) as journal:
        rows=journal.pending('w')
        assert len(rows)==1 and rows[0]['status']=='accepted'
        assert rows[0]['result']==result


@pytest.mark.parametrize('patch',[
    {'army_id':364068},{'destination':[588,1550]},{'command':4},{'auto_return':False},
    {'movement_total_time':299},{'movement_end_time':450},{'observed_at':80},
    {'position':[558,1527]},{'status_codes':[24]}])
def test_missed_packet_invalid_status_keeps_unknown_and_durable_fence(tmp_path,monkeypatch,patch):
    result,calls,m=missed_departure_run(tmp_path,monkeypatch,patch=patch)
    assert calls==['start','attack','roster','close']
    assert result['status']=='unknown' and result['sent'] is True
    with m.AttackJournal(tmp_path) as journal:
        assert len(journal.pending('w'))==1 and journal.pending('w')[0]['status']=='unknown'


@pytest.mark.parametrize('final_patch',[
    {'fault':'heartbeat_lost'},{'cleanup_fault':'detach_unconfirmed'},
    {'bridge_retained':True},{'phase':'quarantined'},{'generation':'wrong-generation'}])
def test_missed_packet_unclean_final_cannot_promote_departure(tmp_path,monkeypatch,final_patch):
    result,calls,m=missed_departure_run(tmp_path,monkeypatch,final_patch=final_patch)
    assert calls==['start','attack','roster','close']
    assert result['status']=='unknown' and result['sent'] is True


def test_missed_packet_status_read_failure_preserves_original_send_receipt(tmp_path,monkeypatch):
    result,calls,m=missed_departure_run(tmp_path,monkeypatch,read_error=True)
    assert calls==['start','attack','roster','close']
    assert result['status']=='unknown' and result['sent'] is True
    assert result['departure_read_error']=='status_read_failed'
    assert result['runtime_generation']=='departure-read'
    with m.AttackJournal(tmp_path) as journal:
        assert journal.pending('w')[0]['result']==result


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


def origin_departure_witness():
    return {'army_id':364067,'army_type':1,'hero_ids':[1,2,3],
            'status_codes':[2],'army_status':2,'system_status':2,
            'command':12,'auto_return':True,'destination':[1438,1192],
            'position':[1440,1236],'dispatch_build_id':65364,
            'movement_total_time':690,'movement_end_time':1793150024,
            'observed_at':1793149346.347,'enemy_army_id':None,'fight_position':None}


def origin_departure_record(journal):
    p=plan();p['position']=[1440,1236];p['base_id']='65364'
    p['target'].update(x=1438,y=1192);p['preview']['need_time']=690
    record=journal.prepare('origin-departure','w',p);journal.claim(record['id'])
    journal.finish(record['id'],{'status':'unknown','sent':True})
    return record


def test_departure_can_still_report_launch_city_with_complete_raw_marching_proof(tmp_path,monkeypatch):
    m=attack_module();monkeypatch.setattr(m.time,'time',lambda:1793149331.23)
    with m.AttackJournal(tmp_path) as journal:
        record=origin_departure_record(journal)
        result=journal.reconcile_departure(record['id'],origin_departure_witness(),now=1793149347)
        assert result['status']=='accepted' and result['sent'] is True
        assert result['departure_verified'] is True and result['encounter_complete'] is False
        assert journal.pending('w')[0]['status']=='accepted'


@pytest.mark.parametrize('patch,omit',[
    ({},'hero_ids'),({},'army_status'),({},'system_status'),({},'dispatch_build_id'),
    ({},'enemy_army_id'),({},'fight_position'),
    ({'hero_ids':[2,1,3]},None),({'hero_ids':[True,2,3]},None),
    ({'army_type':2},None),({'army_status':1},None),({'system_status':1},None),
    ({'dispatch_build_id':11183},None),({'enemy_army_id':8},None),
    ({'fight_position':[1438,1192]},None),({'destination':[1439,1192]},None),
    ({'movement_total_time':689},None),({'movement_end_time':1793150060},None)])
def test_at_origin_departure_requires_every_extra_identity_and_no_battle_witness(tmp_path,monkeypatch,patch,omit):
    m=attack_module();monkeypatch.setattr(m.time,'time',lambda:1793149331.23)
    with m.AttackJournal(tmp_path) as journal:
        record=origin_departure_record(journal);row={**origin_departure_witness(),**patch}
        if omit:row.pop(omit)
        with pytest.raises(ValueError,match='departure_witness_invalid'):
            journal.reconcile_departure(record['id'],row,now=1793149347)
        assert journal.pending('w')[0]['status']=='unknown'


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


def test_field_launch_cannot_use_original_at_base_victory_reconciliation(tmp_path):
    m=attack_module()
    with m.AttackJournal(tmp_path) as store:
        p=plan();p.update(launch_mode='stationary_field',return_destination_verified=False)
        record=store.prepare('field-request','w',p);store.claim(record['id'])
        store.finish(record['id'],{'status':'accepted','sent':True,'departure_verified':True})
        start=store.get(record['id'])['claimed_at'];t=start+300
        proof={'entity_id':'2511112','monster_id':123,'x':587,'y':1550,
               'use_code':1,'conf_id':10,'observed_at':100}
        reports=[{'id':i,'group_id':'123','pos':[587,1550],'player_id':42,'result':1,
                  'hero_ids':[1,2,3],'primary_hero_id':1,'battle_time':t} for i in [1001,1002,1003]]
        post={'army_id':364067,'status_codes':[19],'position':[558,1527],
              'dispatch_build_id':11183,'movement_total_time':0,'movement_end_time':0,
              'observed_at':t+10}
        with pytest.raises(ValueError,match='field_return_destination_unverified'):
            store.record_victory(record['id'],target_witness=proof,reports=reports,
                                 role_id=42,post_status=post,now=t+11)
        assert store.get(record['id'])['result'].get('encounter_complete') is not True
        assert len(store.pending('w'))==1
