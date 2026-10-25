import threading
from types import SimpleNamespace

import pytest

from sgz_agent.client_api import normalize_scan, validate_region, scan


def test_region_has_real_map_and_work_limits():
    assert validate_region(580, 815, 629, 864) == 2500
    for region in [(0, 1, 1, 1), (1, 1, 1621, 1), (1, 1, 51, 51), (True, 1, 2, 2)]:
        with pytest.raises(ValueError):
            validate_region(*region)


def test_resource_and_server_bandit_are_merged_without_claiming_occupancy():
    result = normalize_scan({'resources': [
        {'x': 592, 'y': 830, 'id': 35, 'name': '4级铁矿', 'level': 4},
        {'x': 589, 'y': 829, 'id': 0, 'name': '空地', 'level': 0}],
        'bandits': [{'id': 7, 'x': 589, 'y': 829, 'name': '5级蛮族', 'level': 5, 'conf_id': 15}]})
    assert result[0]['fields']['category'] == '铁矿'
    assert result[1]['fields']['bandit']['level'] == 5
    assert result[0]['fields']['occupancy'] == 'unknown'
    assert result[0]['fields']['bandit_scope'] == 'personal'
    assert result[0]['fields']['bandit_checked'] is False


def test_absent_config_is_unknown_not_guessed_from_monster_id():
    result = normalize_scan({'resources': [{'x': 1, 'y': 2}],
                             'bandits': [{'id': 9, 'x': 1, 'y': 2, 'conf_id': 15}]})
    assert result[0]['fields']['category'] is None
    assert result[0]['fields']['bandit']['level'] is None


def test_conflicting_bandits_are_not_silently_overwritten():
    with pytest.raises(RuntimeError, match='multiple_bandits'):
        normalize_scan({'resources': [{'x': 1, 'y': 2}], 'bandits': [
            {'id': 1, 'x': 1, 'y': 2}, {'id': 2, 'x': 1, 'y': 2}]})


def test_already_cancelled_scan_never_attaches_or_queries_client():
    stop = threading.Event()
    stop.set()
    with pytest.raises(RuntimeError, match='stopped'):
        scan(None, dict(x0=1, y0=1, x1=1, y1=1), stop)


def test_runtime_incident_blocks_all_client_injection_before_attach(tmp_path,monkeypatch):
    import json
    import frida
    from sgz_agent.client_api import readiness
    marker=tmp_path/'hunt-client-quarantine.json'
    marker.write_text(json.dumps({'active':True,'incident_id':'test-incident'}),encoding='utf-8')
    config=SimpleNamespace(data_dir=tmp_path,identity={})
    monkeypatch.setattr(frida,'get_local_device',lambda:pytest.fail('must not attach'))
    assert readiness(config)=='api_runtime_quarantined:test-incident'
    with pytest.raises(RuntimeError,match='api_runtime_quarantined'):
        scan(config,dict(x0=1,y0=1,x1=1,y1=1),threading.Event())


@pytest.mark.parametrize('mode', ['cancel_during_attach', 'late_load'])
def test_frida_lifecycle_cancel_and_timeout_do_not_report_success(monkeypatch, mode):
    import frida
    from pathlib import Path
    from sgz_agent import client_api
    stop = threading.Event()
    actions = []
    clock = [0.0]
    monkeypatch.setattr(client_api, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    class Script:
        def __init__(self): self.exports_sync=SimpleNamespace(close=self.close)
        def close(self):
            actions.append('close')
            return dict(closed=True,active_native=0,callbacks=0,hooks=0)
        def on(self, event, callback): self.callback = callback
        def load(self):
            actions.append('load')
            clock[0] = .06
            for payload in [{'kind': 'chunk', 'items': [{'x': 1, 'y': 1}]},
                            {'kind': 'complete', 'bandits': []}]:
                self.callback({'type': 'send', 'payload': payload}, None)
        def unload(self): actions.append('unload')
    class Session:
        def on(self, *args): pass
        def create_script(self, source): actions.append('create'); return Script()
        def detach(self): actions.append('detach')
    class Device:
        def enumerate_processes(self): return [SimpleNamespace(name='S3Client.exe', pid=123)]
        def attach(self, pid):
            if mode == 'cancel_during_attach': stop.set()
            return Session()
    monkeypatch.setattr(client_api, 'readiness', lambda config: None)
    monkeypatch.setattr(frida, 'get_local_device', lambda: Device())
    config = SimpleNamespace(executable=Path('S3Client.exe'), identity={
        'role_id':'1', 'region':'r', 'api_server_id':'s', 'api_season':1})
    with pytest.raises(RuntimeError, match='stopped' if mode.startswith('cancel') else 'api_scan_timeout'):
        scan(config, dict(x0=1, y0=1, x1=1, y1=1), stop, timeout=.01 if mode == 'late_load' else 1)
    assert 'detach' in actions
    if mode.startswith('cancel'): assert 'load' not in actions
    else: assert actions.index('close') < actions.index('unload') < actions.index('detach')
