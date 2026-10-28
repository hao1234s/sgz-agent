"""Synthetic contract tests; these do not assert live client validation."""
from types import SimpleNamespace
import json
from pathlib import Path
import subprocess

import pytest

from sgz_agent import hunt_client


def raw_army(**changes):
    return {"army_id": 350210, "army_type": 1, "city_id": 144706,
            "status_codes": [1], "position": [591, 828], "is_tying": False,
            "dispatch_build_id": 144706, "hero_ids": [11, 12, 13], **changes}


def test_status_poll_does_not_expose_troops_or_infer_encounter_completion():
    row = hunt_client.normalize_army(raw_army(status_codes=[27], finish_success=True,
                                             troops=123, morale=14))
    assert row["phase"] == "unknown"
    assert row["encounter_complete"] is False
    assert row["completed_target_id"] is None
    assert "troops" not in row and "morale" not in row


@pytest.mark.parametrize(("codes", "tying", "phase"), [
    ([1], False, "idle"), ([3], False, "stationed"), ([2], False, "marching"),
    ([24], False, "fighting"), ([25], False, "draw"), ([3], True, "unknown"),
    ([1, 24], False, "fighting"), ([19], False, "relocating"),
    ([29], False, "returning"), ([999], False, "unknown"),
])
def test_conservative_status_priority(codes, tying, phase):
    assert hunt_client.normalize_army(raw_army(status_codes=codes, is_tying=tying))["phase"] == phase


def test_home_city_is_not_a_separate_deployment_base():
    assert hunt_client.normalize_army(raw_army())["deployment_base_id"] is None
    assert hunt_client.normalize_army(raw_army(dispatch_build_id=44))["deployment_base_id"] == "44"


def arrived_dispatch(**changes):
    # Observed parked city/camp state. Legacy status.dispatch_pos was the home city.
    return raw_army(status_codes=[19], position=[558, 1527], dispatch_build_id=11183,
                    dispatch_position=[558, 1527], dispatch_entered_at=100,
                    movement_end_time=0, movement_total_time=0, observed_at=200,
                    at_dispatch=True, **changes)


def test_arrived_dispatch_is_ready_and_keeps_actual_base_without_ending_battle():
    row = hunt_client.normalize_army(arrived_dispatch())
    assert row['phase'] == 'stationed'
    assert row['current_base_id'] == '11183'
    assert row['deployment_base_id'] == '11183'
    assert row['encounter_complete'] is False


@pytest.mark.parametrize('changes', [
    {'dispatch_position': [559, 1527]}, {'dispatch_position': None},
    {'movement_end_time': 250}, {'movement_total_time': 30},
    {'movement_end_time': None}, {'dispatch_entered_at': 250},
    {'dispatch_entered_at': None}, {'dispatch_entered_at': True},
    {'status_codes': [19, 2]}, {'is_tying': True},
    {'status_codes': [19, 24]}, {'status_codes': [19, 25]},
])
def test_unproven_dispatch_or_active_encounter_never_becomes_ready(changes):
    raw = arrived_dispatch()
    raw.update(changes)
    row = hunt_client.normalize_army(raw)
    assert row['phase'] not in hunt_client.READY_PHASES
    assert row['current_base_id'] is None


def test_native_ready_guard_recognizes_arrived_dispatch_and_rejects_active_states():
    # Execute the real native guard without attaching to a game.
    source = Path(hunt_client.__file__).with_suffix('.js').read_text(encoding='utf-8')
    guard = source[source.index('function isReady(row) {'):source.index('function roster(L) {')]
    rows = [arrived_dispatch()]
    for patch in ({'dispatch_position': None}, {'movement_end_time': 210},
                  {'dispatch_entered_at': None}, {'status_codes': [19, 2]},
                  {'status_codes': [25]}, {'is_tying': True}, {'army_type': 11}):
        rows.append({**arrived_dispatch(), **patch})
    result = subprocess.run(['node', '-e', guard + '\nconsole.log(JSON.stringify(' +
                             json.dumps(rows) + '.map(isReady)))'],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [True, False, False, False, False, False, False, False]


def test_defender_army_identifier_is_not_reserved_as_a_world_monster_target():
    row=hunt_client.normalize_army(raw_army(status_codes=[24],enemy_army_id=901))
    assert row['target_id'] is None
    assert row['enemy_army_id']=='901'
    assert row['encounter_complete'] is False


def test_unformed_and_auxiliary_are_identified():
    assert hunt_client.normalize_army(raw_army(hero_ids=[-1, -1, -1]))["formed"] is False
    assert hunt_client.normalize_army(raw_army(army_type=11))["kind"] == "auxiliary"


def test_ready_read_for_active_army_is_rejected_before_detail_call(monkeypatch):
    client = hunt_client.HuntClient(SimpleNamespace(identity={"world": "w"}))
    calls = []
    def read(operation, **params):
        calls.append(operation)
        if operation != "roster":
            pytest.fail("battle army detail read")
        return {"complete": True, "armies": [raw_army(status_codes=[25])]}
    monkeypatch.setattr(client, "_read", read)
    with pytest.raises(RuntimeError, match="army_not_ready_for_details"):
        client.ready_army("350210")
    assert calls == ["roster"]


def test_unverified_actions_never_touch_transport(monkeypatch):
    client = hunt_client.HuntClient(SimpleNamespace(identity={"world": "w"}))
    monkeypatch.setattr(client, "_read", lambda *a, **k: pytest.fail("mutation reached bridge"))
    result = client.execute({"id": "one", "request_id": "one", "army_id": "350210",
                             "kind": "attack", "payload": {"target_id": "t"}})
    assert result["status"] == "rejected"
    assert result["sent"] is False
    assert client.reconcile({}, {}) == "unknown"


def test_replenishment_uses_owned_hunt_journal_and_maps_verified_receipt(monkeypatch):
    from sgz_agent import replenish, replenish_client
    calls=[]
    class Native:
        def __init__(self,*a,**k): pass
        def ensure_execution_ready(self): calls.append('gate')
    class Worker:
        def __init__(self,*a,**k): pass
        def run_for_hunt(self,**kw):
            calls.append(kw)
            return {'armies':[{'army_id':'350212','status':'applied','sent':True,'command_id':'child'}]}
    monkeypatch.setattr(replenish_client,'ReplenishClient',Native)
    monkeypatch.setattr(replenish,'Replenisher',Worker)
    client=hunt_client.HuntClient(SimpleNamespace(identity={'world':'w'}))
    receipt=client.execute({'id':'parent','hunt_id':'h','army_id':'350212','kind':'replenish'})
    assert receipt['status']=='applied' and receipt['replenish_command_id']=='child'
    assert calls==['gate',{'hunt_id':'h','command_id':'parent'}]


def test_resource_shortage_is_known_unsent_and_retryable(monkeypatch):
    from sgz_agent import replenish, replenish_client
    class Native:
        def __init__(self,*a,**k): pass
        def ensure_execution_ready(self): pass
    class Worker:
        def __init__(self,*a,**k): pass
        def run_for_hunt(self,**kw):
            return {'armies':[{'army_id':'350212','status':'blocked','sent':False,'reason':'reserve_insufficient'}]}
    monkeypatch.setattr(replenish_client,'ReplenishClient',Native)
    monkeypatch.setattr(replenish,'Replenisher',Worker)
    client=hunt_client.HuntClient(SimpleNamespace(identity={'world':'w'}))
    receipt=client.execute({'id':'parent','hunt_id':'h','army_id':'350212','kind':'replenish'})
    assert receipt['status']=='not_applied' and receipt['retryable'] is True and receipt['sent'] is False


def test_search_filters_radius_taxonomy_and_does_not_upgrade_incomplete_coverage(monkeypatch):
    client = hunt_client.HuntClient(SimpleNamespace(identity={"world": "w"}))
    monkeypatch.setattr(client, "_read", lambda *a, **k: {
        "complete": False, "targets": [
            {"id": 1, "x": 660, "y": 828, "use_code": 1, "conf_id": 10},
            {"id": 2, "x": 661, "y": 829, "use_code": 1, "conf_id": 10},
            {"id": 3, "x": 591, "y": 828, "use_code": 3, "conf_id": 10},
        ]})
    found = client.search_giants([591, 828], radius=70)
    assert [r["id"] for r in found["targets"]] == ["1"]
    assert found["complete"] is False


def test_fresh_search_passes_guarded_army_and_correlates_stream(tmp_path, monkeypatch):
    import time
    now = time.time() - 1
    client = hunt_client.HuntClient(SimpleNamespace(identity={'world':'w'}, data_dir=tmp_path))
    calls = []
    def read(operation, **params):
        calls.append((operation, params))
        sid = params['session_seed']
        return {'sessions':[{'session_id':sid,'center':[591,828],'sent_at':now,'finished_at':now+0.1}],
                'events':[{'name':'sc_notify_refresh_aoi_content','received_at':now+0.05,
                    'data':{'session_id':sid,'info_list':[{'id':4,'proto_type':'add','monster':{
                        'use_code':1,'conf_id':10,'pos':{'row':590,'col':827},'end_time':now+600}}]}}]}
    monkeypatch.setattr(client, '_read', read)
    result = client.search_giants([591,828], army_id='350212')
    assert calls[0][0] == 'search_fresh'
    assert calls[0][1]['army_id'] == 350212
    assert result['complete'] is False
    assert [target['id'] for target in result['targets']] == ['4']
    first_seed = calls[0][1]['session_seed']
    client.search_giants([591,828], army_id='350212')
    assert calls[1][1]['session_seed'] >= first_seed + len(calls[0][1]['centers'])


def test_expired_cached_target_is_never_offered(monkeypatch):
    client = hunt_client.HuntClient(SimpleNamespace(identity={'world':'w'}))
    monkeypatch.setattr(client, '_read', lambda *a, **k: {'complete':False,'targets':[
        {'id':1,'x':591,'y':828,'use_code':1,'conf_id':10,'end_time':1}]})
    assert client.search_giants([591,828])['targets'] == []


@pytest.mark.parametrize("center,radius", [([0, 4], 70), ([1, 1], 71), ([True, 4], 70)])
def test_invalid_search_never_calls_client(monkeypatch, center, radius):
    client = hunt_client.HuntClient(SimpleNamespace(identity={"world": "w"}))
    monkeypatch.setattr(client, "_read", lambda *a, **k: pytest.fail("invalid search sent"))
    with pytest.raises(ValueError):
        client.search_giants(center, radius)


def test_incident_quarantine_blocks_reads_before_client_attach(tmp_path):
    (tmp_path / "hunt-client-quarantine.json").write_text(
        '{"active":true,"incident_id":"actual-frida-crash"}', encoding="utf-8")
    client = hunt_client.HuntClient(SimpleNamespace(data_dir=tmp_path, identity={"world": "w"}))
    with pytest.raises(RuntimeError, match="hunt_runtime_quarantined"):
        client.roster_status()


def test_preview_request_is_narrow_and_uses_verified_taofa_enum():
    assert hunt_client.preview_request("350210", [579, 822], kind="attack") == {
        "army_id": 350210, "target_pos": {"row": 579, "col": 822},
        "cmd": 12, "cmd_target_type": 1}
    with pytest.raises(ValueError):
        hunt_client.preview_request("350210", [579, 822], kind="buy")


def test_server_preview_time_is_preserved_without_inventing_morale():
    data = hunt_client.normalize_preview({"result": 1, "need_time": 150, "path_len": 13,
                                         "need_energy": 15, "speed": 120}, "target")
    assert data["travel_seconds"] == 0
    assert data["prepare_seconds"] == 0
    assert data["attack_seconds"] == 150
    assert data["command_prepare_seconds"] is None
    assert data["arrival_morale"] is None and data["verified"] is False
    assert data["target_id"] == "target"


def test_command_preparation_is_retained_without_claiming_a_verified_route():
    data=hunt_client.normalize_preview({'result':1,'need_time':150,'prepare_time':20},'target')
    assert data['attack_seconds']==150
    assert data['command_prepare_seconds']==20
    assert data['verified'] is False
    assert 'command_prepare_semantics' in data['missing']


def test_rejected_server_preview_cannot_become_feasible_route():
    assert hunt_client.normalize_preview({"result": 2, "need_time": 100}, "target")["feasible"] is False


def test_attack_draft_carries_verified_schema_but_never_claims_execution():
    command = {"id": "r", "request_id": "r", "army_id": "350210", "kind": "attack",
               "payload": {"target": {"id": "1983909", "x": 579, "y": 822,
                                       "use_code": 1, "conf_id": 10}}}
    prepared = hunt_client.prepare_protocol_command(command)
    assert prepared["protocol"] == "cs_army_cmd"
    assert prepared["request"]["cmd"] == 12
    assert prepared["request"]["army_id"] == 350210
    assert prepared["execution_verified"] is False
    assert prepared["sent"] is False
    command["payload"]["target"]["use_code"] = 3
    with pytest.raises(ValueError, match="target_not_giant"):
        hunt_client.prepare_protocol_command(command)


def test_replenishment_draft_cannot_target_an_unlisted_or_excess_hero():
    command = {"id": "r", "army_id": "350210", "kind": "replenish", "payload": {
        "cons_index": 1, "heroes": [{"id": "17", "troops": 20, "capacity": 500}]}}
    prepared = hunt_client.prepare_protocol_command(command)
    assert prepared["request"]["hero_id_list"] == [17]
    assert prepared["request"]["hero_troop_list"] == [500]
    assert prepared["execution_verified"] is False
    command["payload"]["heroes"][0]["troops"] = 501
    with pytest.raises(ValueError, match="hero_troops_invalid"):
        hunt_client.prepare_protocol_command(command)


def test_unproven_tying_flag_does_not_label_idle_army_as_battle_draw():
    row = hunt_client.normalize_army(raw_army(is_tying=True, status_codes=[1]))
    assert row["phase"] == "unknown"
    assert row["status_reason"] == "tying_flag_semantics_unverified"


def test_consistent_idle_getters_and_zero_movement_resolve_lingering_flag():
    raw=arrived_dispatch(is_tying=True,army_status=19,system_status=19)
    assert hunt_client.normalize_army(raw)['phase']=='stationed'
    source=Path(hunt_client.__file__).with_suffix('.js').read_text(encoding='utf-8')
    guard=source[source.index('function isReady(row) {'):source.index('function roster(L) {')]
    rows=[raw,{**raw,'system_status':25},{**raw,'movement_total_time':1},
          {**raw,'status_codes':[25]},{**raw,'army_status':None}]
    result=subprocess.run(['node','-e',guard+';console.log(JSON.stringify('+json.dumps(rows)+'.map(isReady)))'],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)==[True,False,False,False,False]
    for r in rows[1:]:assert hunt_client.normalize_army(r)['phase'] not in ('idle','stationed')


@pytest.mark.parametrize('code,phase',[(2,'marching'),(4,'returning'),(19,'relocating')])
def test_consistent_active_getters_preserve_active_phase(code,phase):
    row=raw_army(status_codes=[code],army_status=code,system_status=code,is_tying=True,
                  movement_total_time=100,movement_end_time=200)
    assert hunt_client.normalize_army(row)['phase']==phase


def test_native_decoder_reads_first_of_multiple_lua_return_values():
    # Executes the actual JS helper in an isolated VM. Native wrappers are inert;
    # no Frida process or game connection exists in this synthetic regression.
    program = r'''
const fs=require('fs'),vm=require('vm');
const context={OPTIONS:{path:'/verified.exe'},Process:{getModuleByName(){return {
 path:'/verified.exe',getExportByName(name){return name;}};}},
 NativeFunction:function(){return function(){};},Interceptor:{attach(){return {detach(){}};},flush(){}},
 rpc:{exports:{}},setImmediate(fn){},send(){}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(require('path').join(require('path').dirname(process.argv[1]),'native_lifecycle.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const data=vm.runInContext(`[
 decodedReturnIndex(10,1),decodedReturnIndex(10,2),decodedReturnIndex(10,3),
 (()=>{try{decodedReturnIndex(10,0);return 'accepted';}catch(e){return 'rejected';}})(),
 (()=>{try{decodedReturnIndex(2,3);return 'accepted';}catch(e){return 'rejected';}})()
]`,context);process.stdout.write(JSON.stringify(data));
'''
    result = subprocess.run(["node", "-e", program,
                             str(Path(hunt_client.__file__).with_suffix(".js"))],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [10, 9, 8, "rejected", "rejected"]


@pytest.mark.parametrize('drains',[True,False])
def test_native_cleanup_requires_confirmed_quiescence(monkeypatch,tmp_path,drains):
    import frida
    actions=[]
    class Script:
        def __init__(self): self.exports_sync=SimpleNamespace(close=self.close)
        def on(self,event,callback): self.callback=callback
        def load(self): self.callback({'type':'send','payload':{'kind':'complete','data':{'ok':True}}},None)
        def close(self):
            actions.append('close')
            return {'closed':drains,'active_native':0 if drains else 1,'callbacks':0,'hooks':0}
        def unload(self): actions.append('unload')
    class Session:
        def on(self,*args):pass
        def create_script(self,source):return Script()
        def detach(self):actions.append('detach')
    class Device:
        def enumerate_processes(self):return [SimpleNamespace(name='S3Client.exe',pid=123)]
        def attach(self,pid):return Session()
    monkeypatch.setattr(hunt_client,'readiness',lambda config:None)
    monkeypatch.setattr(frida,'get_local_device',lambda:Device())
    config=SimpleNamespace(executable=Path('S3Client.exe'),data_dir=tmp_path,identity={
        'role_id':'1','region':'r','api_server_id':1,'api_season':1})
    client=hunt_client.HuntClient(config)
    if drains:
        result = client._read('roster')
        assert result['ok'] is True and result['cleanup_verified'] is True
        assert actions==['close','unload','detach']
    else:
        with pytest.raises(RuntimeError,match='quiescence'):
            client._read('roster')
        assert actions==['close']
        assert client._quarantine()['active'] is True
def test_preview_waypoints_preserve_order_and_reject_sparse_or_invalid_native_rows():
    import json, subprocess
    from pathlib import Path
    source=Path('src/sgz_agent/hunt_client.js').read_text(encoding='utf-8')
    start=source.index('function validatePreviewWaypoints(')
    end=source.index('\nfunction readPreviewWaypoints(',start)
    program=source[start:end]+'''\n
const assert=require('node:assert/strict');
assert.deepEqual(validatePreviewWaypoints([[2,[579,822]],[1,[591,828]]]),[[591,828],[579,822]]);
for(const rows of [[[2,[10,10]]],[[1,[10,10]],[1,[11,11]]],[[1,[0,10]]],[[1,[10.5,10]]]])
  assert.throws(()=>validatePreviewWaypoints(rows));
'''
    subprocess.run(['node','-e',program],check=True,capture_output=True,text=True)
