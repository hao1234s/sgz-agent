from dataclasses import replace
from types import SimpleNamespace

import pytest
from PIL import Image

from sgz_agent.config import AgentConfig
from sgz_agent.models import TextBox
from sgz_agent.service import AgentService


@pytest.fixture
def service(tmp_path, monkeypatch):
    svc = AgentService(AgentConfig(tmp_path/'game.exe', tmp_path, identity={'world':'w'}),
                       desktop=SimpleNamespace(release=lambda:None, list_windows=lambda exe:[]))
    monkeypatch.setattr(svc, '_listen_stop', lambda: None)
    yield svc
    svc.close()


def test_scan_dispatch_preserves_request_id_and_point_plan(service):
    entered=[]
    def inspect(job):
        entered.append(job)
        service.stop_event.wait(1)
    service._explore=inspect
    args={'request_id':'scan','world':'w','x0':0,'y0':0,'x1':5,'y1':5,'source':'ui'}
    job=service.dispatch('scan_region',args)
    assert len(job['payload']['points'])==36
    assert service.dispatch('scan_region',args)['id']==job['id']
    service.dispatch('pause_task',{'job_id':job['id']})
    with pytest.raises(ValueError,match='request_conflict'):
        service.dispatch('scan_region',{**args,'x1':4})


@pytest.mark.parametrize('source', [None, 'api'])
def test_api_scan_never_silently_falls_back_to_ui(service, source):
    args={'request_id':'api','world':'w','x0':0,'y0':0,'x1':5,'y1':5}
    if source is not None:
        args['source']=source
    with pytest.raises(RuntimeError, match='map_api_unavailable:api_identity_binding_required'):
        service.dispatch('scan_region', args)
    assert service.store.list_jobs() == []
    assert service.controller is None


def test_unknown_scan_source_does_not_start_a_job(service):
    with pytest.raises(ValueError, match='invalid_scan_source'):
        service.dispatch('scan_region', dict(request_id='bad',world='w',x0=0,y0=0,x1=0,y1=0,source='automatic'))
    assert service.store.list_jobs() == []


def test_protocol_status_distinguishes_packet_capture_from_a_working_adapter(service):
    status=service.dispatch('protocol_status', {})
    assert status['api_scan_available'] is False
    assert status['reason'] == 'api_identity_binding_required'
    assert status['ui_fallback_automatic'] is False
    assert service.dispatch('game_status', {})['capabilities']['map_api_scan'] is False


def test_combat_assessment_has_no_desktop_or_job_side_effects(service):
    result = service.dispatch('assess_combat', {'phase': 'fighting'})
    assert result['decision'] == 'wait'
    assert service.store.list_jobs() == []
    assert service.controller is None
    assert service.dispatch('game_status', {})['capabilities']['combat_assessment'] is True


def test_hunt_tools_keep_stop_separate_from_game_withdrawal(service, monkeypatch):
    calls=[]
    class Client:
        def capabilities(self): return {'available':True,'missing':[]}
        def roster_status(self):
            return {'world':'w','complete':True,'armies':[
                {'id':'a','kind':'regular','home_city_id':'home','phase':'fighting'}]}
        def execute(self, command): calls.append(command)
    monkeypatch.setattr(service,'_make_hunt_client',lambda:Client())
    started=service.dispatch('hunt_start',{'world':'w','request_id':'all'})
    stopped=service.dispatch('hunt_stop',{'hunt_id':started['id']})
    assert stopped['status']=='paused' and calls==[]
    assert service.dispatch('hunt_status',{'hunt_id':started['id']})['campaigns'][0]['status']=='paused'
    assert service.controller is None


def test_shutdown_preserves_running_hunt_but_emergency_stop_pauses_it(service, monkeypatch):
    class Client:
        def capabilities(self): return {'available':True,'missing':[]}
        def roster_status(self):
            return {'world':'w','complete':True,'armies':[
                {'id':'a','kind':'regular','home_city_id':'home','phase':'fighting'}]}
    monkeypatch.setattr(service,'_make_hunt_client',lambda:Client())
    started=service.dispatch('hunt_start',{'world':'w','request_id':'all'})
    service._stop()
    assert service.dispatch('hunt_status',{})['campaigns'][0]['status']=='paused'
    service.dispatch('hunt_resume',{'hunt_id':started['id']})
    service.request_stop()
    assert service.hunt_store.get(started['id'])['status']=='running'


def test_ready_api_scan_uses_api_job_and_persists_provenance(service, monkeypatch):
    from sgz_agent import client_api
    monkeypatch.setattr(client_api, 'readiness', lambda config: None)
    monkeypatch.setattr(client_api, 'scan', lambda *args, **kwargs: {
        'resources': [{'x': 1, 'y': 2, 'name': '4级铁矿', 'level': 4}], 'bandits': [],
        'elapsed_seconds': .5, 'coverage': {'world_bandits_complete': False}})
    job = service.dispatch('scan_region', dict(request_id='api', world='w', x0=1, y0=2, x1=1, y1=2))
    service.job_thread.join(5)
    assert service.store.get_job(job['id'])['status'] == 'complete'
    assert service.controller is None
    result = service.dispatch('search_map', dict(world='w', x0=1, y0=2, x1=1, y1=2, kind='resource'))
    assert result['results'][0]['source'] == 'client_api'
    assert result['results'][0]['occupancy'] == 'unknown'
    assert result['coverage']['world_bandits_complete'] is False


def test_api_stop_listener_does_not_bind_or_activate_window(service, monkeypatch):
    from sgz_agent import windows
    listeners = []
    class Listener:
        def __init__(self, callback): self.callback = callback
        def start(self): listeners.append(self); return self
        def close(self): pass
    monkeypatch.setattr(windows, 'StopListener', Listener)
    AgentService._listen_stop(service)
    assert len(listeners) == 1 and service.controller is None
    listeners[0].callback()
    assert service.stop_event.is_set()


@pytest.mark.parametrize('method,args',[
    ('search_map',dict(x0=0,y0=0,x1=0,y1=0)),('query_roster',{}),
    ('refresh_roster',dict(request_id='roster'))])
def test_new_tools_do_not_mix_worlds(service,method,args):
    with pytest.raises(ValueError,match='world_mismatch'):
        service.dispatch(method,{'world':'wrong',**args})


def test_roster_refresh_checks_identity_and_persists_snapshot(service):
    profile={'crops':[], 'anchors':['城池列表'],
             'identity_checks':[{'text':'本角色','region':[.8,0,1,.1]}],
             'cities':[{'id':'main','kind':'main','name_region':[0,.1,.2,.2],
                        'coordinate_region':[.2,.1,.4,.2]}],
             'total_cities_region':[0,.5,.2,.6],'total_armies_region':[.2,.5,.4,.6]}
    service.config=replace(service.config,ui={'roster':{'pages':[profile]}})
    verified=[]
    class UI:
        def activate(self): pass
        def capture(self): return SimpleNamespace(image=Image.new('RGB',(20,20)))
        def read(self,frame,crops):
            return [TextBox('城池列表',.99,(.4,0,.6,.1)),TextBox('本角色',.99,(.85,0,.95,.1)),
                    TextBox('主城',.99,(.01,.11,.19,.19)),TextBox('(100,200)',.99,(.21,.11,.39,.19)),
                    TextBox('城池总数1座',.99,(0,.5,.2,.6)),TextBox('队伍总数0支',.99,(.2,.5,.4,.6))]
    service._navigator=lambda:SimpleNamespace(ui=UI(),verify_identity=lambda:verified.append(True))
    job=service.store.create_job('roster','roster',{'world':'w'})
    service._refresh_roster(job)
    result=service.dispatch('query_roster',{'world':'w'})
    assert verified==[True] and result['complete'] is True
    assert result['cities'][0]['coordinate']==[100,200]
    assert service.store.get_job(job['id'])['status']=='complete'


def test_failed_refresh_invalidates_current_roster_until_a_complete_new_read(service):
    service.store.save_roster('w',{'cities':[],'armies':[],'complete':True},['old.png'])
    service.config=replace(service.config,ui={'roster':{'pages':[{'actions':[]}]}})
    service._navigator=lambda:SimpleNamespace(ui=SimpleNamespace(activate=lambda:None),
        verify_identity=lambda:(_ for _ in ()).throw(RuntimeError('identity_mismatch')))
    job=service.store.create_job('roster','failed',{'world':'w'})
    service._run(job)
    result=service.dispatch('query_roster',{'world':'w'})
    assert result['refresh_status']=='paused'
    assert result['refresh_error']=='identity_mismatch'
    assert result['refresh_required'] is True
