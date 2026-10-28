import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


def test_native_send_guard_rejects_changed_army_state_and_arbitrary_counts():
    path=Path(__file__).parents[1]/'src/sgz_agent/replenish_client.js'
    assert path.exists(), 'native replenishment adapter must exist'
    code=r'''
const fs=require('fs'),vm=require('vm');
const source=fs.readFileSync(process.argv[1],'utf8').split('// Native adapter')[0];
const c={};vm.createContext(c);vm.runInContext(source,c);
const result=vm.runInContext(`(()=>{
 const row={army_id:'11',phase:'idle',home_city_id:'10',current_base_id:'10',ordinary_verified:true,
  recruitment_clear:true,reserve:5000,heroes:[{id:'101',troops:2000,capacity:5000}]};
 const plan={army_id:'11',home_city_id:'10',heroes:[{id:'101',troops:2000,capacity:5000}],
  request:{army_id:11,cons_index:1,hero_id_list:[101],hero_troop_list:[5000]}};
 const check=(p,r)=>validateReplenish(p,r);
 return [check(plan,row),check(plan,{...row,phase:'fighting'}),
  check(plan,{...row,reserve:2999}),check(plan,{...row,current_base_id:'12'}),
  check(plan,{...row,heroes:[{id:'101',troops:2500,capacity:5000}]}),
  check({...plan,request:{...plan.request,hero_troop_list:[6000]}},row),
  check(plan,{...row,heroes:[{id:'101',troops:5000,capacity:5000}]}),
  check(plan,{...row,ordinary_verified:false})];
})()`,c);console.log(JSON.stringify(result));
'''
    result=subprocess.run(['node','-e',code,str(path)],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)==[None,'army_not_idle','reserve_insufficient','not_in_own_city',
                                      'troop_snapshot_changed','invalid_replenish_request','already_full','ordinary_reserve_unverified']


def test_scoped_validation_does_not_remove_hunting_quarantine(tmp_path,monkeypatch):
    from sgz_agent import replenish_client as module
    from sgz_agent.client_api import readiness
    exe=tmp_path/'S3Client.exe';exe.write_bytes(b'fixture')
    config=SimpleNamespace(executable=exe,data_dir=tmp_path,identity={
        'world':'w','role_id':'1','region':'r','api_server_id':1,'api_season':1})
    marker=tmp_path/'hunt-client-quarantine.json'
    marker.write_text('{"active":true,"incident_id":"original"}',encoding='utf-8')
    monkeypatch.setattr(module,'_fingerprint',lambda *a:module.SUPPORTED_SHA256)
    assert module.replenish_readiness(config)=='replenish_validation_required'
    module.save_validation(config,evidence='fixture:success')
    assert module.replenish_readiness(config) is None
    assert readiness(config).startswith('api_runtime_quarantined')
    marker.write_text('{"active":true,"incident_id":"new_failure"}',encoding='utf-8')
    assert module.replenish_readiness(config)=='replenish_incident_changed'


def test_scope_record_is_bound_to_identity_and_revocation(tmp_path,monkeypatch):
    from sgz_agent import replenish_client as module
    exe=tmp_path/'S3Client.exe';exe.write_bytes(b'fixture')
    config=SimpleNamespace(executable=exe,data_dir=tmp_path,identity={
        'world':'w','role_id':'1','region':'r','api_server_id':1,'api_season':1})
    monkeypatch.setattr(module,'_fingerprint',lambda *a:module.SUPPORTED_SHA256)
    module.save_validation(config,evidence='fixture:success')
    config.identity['role_id']='2'
    assert module.replenish_readiness(config)=='replenish_identity_changed'
    config.identity['role_id']='1'
    module.revoke_validation(config,'native_failure')
    assert module.replenish_readiness(config)=='replenish_validation_required'


def test_unvalidated_adapter_does_not_attach(tmp_path):
    from sgz_agent.replenish_client import ReplenishClient
    config=SimpleNamespace(executable=tmp_path/'missing.exe',data_dir=tmp_path,identity={'world':'w'})
    with pytest.raises(RuntimeError):ReplenishClient(config).snapshot()


def test_reserve_selection_uses_army_status_not_hero_imperial_flags():
    path=Path(__file__).parents[1]/'src/sgz_agent/replenish_client.js'
    code=r'''
const fs=require('fs'),vm=require('vm'),c={};vm.createContext(c);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').split('// Native adapter')[0],c);
console.log(JSON.stringify(vm.runInContext(`[
 ordinaryReserveVerified({is_person_guard:false,person_guard_index:0}),
 ordinaryReserveVerified({is_person_guard:true,person_guard_index:3,imperial_commander:false,imperial_soldier:false}),
 ordinaryReserveVerified({is_person_guard:true,person_guard_index:2}),
 ordinaryReserveVerified({is_person_guard:false,person_guard_index:2}),
 ordinaryReserveVerified({person_guard_index:0}),ordinaryReserveVerified({is_person_guard:false})]`,c)));
'''
    result=subprocess.run(['node','-e',code,str(path)],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)==[True,False,False,False,False,False]


def load_verifier():
    import importlib.util
    path=Path(__file__).parents[1]/'scripts/Test-ReplenishAPI.py'
    spec=importlib.util.spec_from_file_location('replenish_verifier',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_native_ack_policy_accepts_missing_reentrant_observation_but_requires_both_acks():
    path=Path(__file__).parents[1]/'src/sgz_agent/replenish_client.js'
    code=r'''
const fs=require('fs'),vm=require('vm'),c={};vm.createContext(c);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8').split('// Native adapter')[0],c);
console.log(JSON.stringify(vm.runInContext(`(()=>{
 const s={native_calls:1,encoded:0,ambiguous:false,army_ack:null,response_ack:null};
 return [replenishAckDecision(s),replenishAckDecision({...s,army_ack:true}),
 replenishAckDecision({...s,army_ack:true,response_ack:true}),
 replenishAckDecision({...s,ambiguous:true}),replenishAckDecision({...s,encoded:2}),
 replenishAckDecision({...s,native_calls:2}),replenishAckDecision({...s,army_ack:false})];
})()`,c)));
'''
    r=subprocess.run(['node','-e',code,str(path)],capture_output=True,text=True,check=True)
    assert json.loads(r.stdout)==['wait','wait','verify','unknown','unknown','unknown','rejected']


def test_validation_cannot_be_restored_from_cached_success(tmp_path,monkeypatch):
    module=load_verifier();grants=[]
    config=SimpleNamespace(data_dir=tmp_path,identity={'world':'w'})
    monkeypatch.setattr(module,'ReplenishClient',lambda *a,**k:SimpleNamespace(execute=lambda p:None))
    monkeypatch.setattr(module,'Replenisher',lambda *a,**k:SimpleNamespace(run=lambda **k:{'armies':[{'status':'applied'}]}))
    monkeypatch.setattr(module,'save_validation',lambda *a,**k:grants.append(k))
    with pytest.raises(RuntimeError,match='Cannot validate'):
        module.verify(config,SimpleNamespace(request_id='old',execute_army='11',grant_validation=True))
    assert grants==[]


@pytest.mark.parametrize('entry',['normal','verification','reconciliation'])
def test_standalone_refill_refuses_running_worker_before_client_read(tmp_path,monkeypatch,entry):
    from contextlib import closing
    from sgz_agent.lease import DataLease
    from sgz_agent import cli
    module=load_verifier() if entry=='verification' else cli
    monkeypatch.setattr(module,'load_config',lambda *a:SimpleNamespace(data_dir=tmp_path,identity={'world':'w'}))
    argv=['agent','replenish','--world','w','--request-id','x'] if entry=='normal' else ['verify','--approved-controlled-session']
    if entry=='reconciliation':argv=['agent','reconcile-replenish','--world','w','--command-id','c']
    monkeypatch.setattr('sys.argv',argv)
    with closing(DataLease(tmp_path)),pytest.raises(RuntimeError,match='data_directory_busy'):
        module.main()


def validation_config(tmp_path, monkeypatch):
    from sgz_agent import replenish_client as module
    exe=tmp_path/'S3Client.exe';exe.write_bytes(b'fixture')
    config=SimpleNamespace(executable=exe,data_dir=tmp_path,identity={
        'world':'w','role_id':'1','region':'r','api_server_id':1,'api_season':1})
    monkeypatch.setattr(module,'_fingerprint',lambda *a:module.SUPPORTED_SHA256)
    return module,config


def test_snapshot_validation_allows_observation_without_authorizing_execute(tmp_path,monkeypatch):
    module,config=validation_config(tmp_path,monkeypatch)
    module.save_validation(config,evidence='fixture:read',operation='snapshot')
    assert module.replenish_readiness(config,operation='snapshot') is None
    assert module.replenish_readiness(config)=='replenish_validation_required'
    with pytest.raises(RuntimeError,match='replenish_validation_required'):
        module.ReplenishClient(config).execute({})
    config.identity['role_id']='different'
    assert module.replenish_readiness(config,operation='snapshot')=='replenish_identity_changed'


def test_unknown_write_keeps_read_scope_but_native_failure_revokes_both(tmp_path,monkeypatch):
    module,config=validation_config(tmp_path,monkeypatch)
    module.save_validation(config,evidence='fixture:read',operation='snapshot')
    module.save_validation(config,evidence='fixture:write')
    module.revoke_validation(config,'unknown_result')
    assert module.replenish_readiness(config)=='replenish_validation_required'
    assert module.replenish_readiness(config,operation='snapshot') is None
    module.revoke_validation(config,'native_failure',include_read=True)
    assert module.replenish_readiness(config,operation='snapshot')=='replenish_read_validation_required'


def test_write_validation_implies_read_permission_and_invalid_operation_is_rejected(tmp_path,monkeypatch):
    module,config=validation_config(tmp_path,monkeypatch)
    module.save_validation(config,evidence='fixture:write')
    assert module.replenish_readiness(config,operation='snapshot') is None
    with pytest.raises(ValueError,match='replenish_operation_not_allowed'):
        module.replenish_readiness(config,operation='arbitrary')


@pytest.mark.parametrize('change',[
    {}, {'cleanup_verified':False}, {'identity':{'world':'other'}}, {'complete':False},
    {'native_lifecycle':{'closed':True,'active_native':1,'callbacks':0,'hooks':0}},
])
def test_read_validation_requires_clean_bound_live_observation(tmp_path,monkeypatch,change):
    verifier=load_verifier();module,config=validation_config(tmp_path,monkeypatch)
    snapshot={'identity':config.identity,'complete':True,'cleanup_verified':True,
              'native_lifecycle':{'closed':True,'active_native':0,'callbacks':0,'hooks':0},
              'armies':[],**change}
    class ReadClient:
        def snapshot(self):return snapshot
        def execute(self,plan):raise AssertionError('read verification sent a command')
    monkeypatch.setattr(verifier,'ReplenishClient',lambda *a,**k:ReadClient())
    args=SimpleNamespace(request_id='read-only',execute_army=None,grant_validation=False,grant_read_validation=True)
    if not change:
        verifier.verify(config,args)
        assert module.replenish_readiness(config,operation='snapshot') is None
        assert module.replenish_readiness(config)=='replenish_validation_required'
    else:
        with pytest.raises(RuntimeError,match='Cannot validate'):
            verifier.verify(config,args)
        assert not module.validation_path(config,'snapshot').exists()
