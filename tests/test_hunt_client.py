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
    assert data["travel_seconds"] == 150
    assert data["prepare_seconds"] is None
    assert data["arrival_morale"] is None and data["verified"] is False
    assert data["target_id"] == "target"


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
        assert client._read('roster')=={'ok':True}
        assert actions==['close','unload','detach']
    else:
        with pytest.raises(RuntimeError,match='quiescence'):
            client._read('roster')
        assert actions==['close']
        assert client._quarantine()['active'] is True
