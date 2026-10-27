import threading
from types import SimpleNamespace
import pytest


def make_service(tmp_path):
    from sgz_agent.service import AgentService
    from sgz_agent.config import AgentConfig
    desktop = SimpleNamespace(release=lambda: None, list_windows=lambda exe: [])
    return AgentService(AgentConfig(executable=tmp_path / 'game.exe', data_dir=tmp_path), desktop=desktop)


def test_duplicate_job_does_not_launch_second_executor_and_pause_is_responsive(tmp_path):
    service = make_service(tmp_path)
    entered = threading.Event()
    count = []
    def login(job):
        count.append(job['id'])
        entered.set()
        service.stop_event.wait(2)
    service._login = login
    job = service.dispatch('prepare_login', {'request_id': 'one'})
    assert entered.wait(1)
    duplicate = service.dispatch('prepare_login', {'request_id': 'one'})
    assert duplicate['id'] == job['id']
    paused = service.dispatch('pause_task', {'job_id': job['id']})
    assert paused['status'] == 'paused'
    service.close()
    assert count == [job['id']]


def test_close_during_recognition_does_not_allow_new_input_job(tmp_path):
    service = make_service(tmp_path)
    service.request_stop()
    with pytest.raises(RuntimeError, match='service_closed'):
        service._start('login', 'late', {})
    service.close()


def test_observation_reserves_controller_until_recognition_finishes(tmp_path):
    service = make_service(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def observe():
        entered.set()
        assert release.wait(2)
        return SimpleNamespace(kind='unknown'), SimpleNamespace(kind='unknown')
    service._observe = observe
    # Exercise reservation independently of the image payload serialization.
    errors = []
    def read():
        try:
            service.dispatch('observe_map', {})
        except Exception as e:
            errors.append(e)
    reader = threading.Thread(target=read)
    reader.start()
    assert entered.wait(1)
    try:
        with pytest.raises(RuntimeError, match='observation_busy'):
            service.dispatch('prepare_login', {'request_id': 'during-ocr'})
    finally:
        release.set()
        reader.join(2)
        service.close()


def test_pausing_completed_job_preserves_completion(tmp_path):
    service = make_service(tmp_path)
    job = service.store.create_job('login', 'done', {})
    service.store.update_job(job['id'], 'complete')
    assert service.dispatch('pause_task', {'job_id': job['id']})['status'] == 'complete'
    service.close()


def test_second_worker_cannot_recover_jobs_owned_by_live_worker(tmp_path):
    service = make_service(tmp_path)
    job = service.store.create_job('login', 'live', {})
    service.store.update_job(job['id'], 'running')
    try:
        with pytest.raises(RuntimeError, match='data_directory_busy'):
            make_service(tmp_path)
        assert service.store.get_job(job['id'])['status'] == 'running'
    finally:
        service.close()
    restarted = make_service(tmp_path)
    assert restarted.store.get_job(job['id'])['status'] == 'paused'
    restarted.close()


def test_exploration_rejects_different_world_before_starting_job(tmp_path):
    from dataclasses import replace
    service=make_service(tmp_path)
    service.config=replace(service.config, identity={'world':'expected'})
    try:
        with pytest.raises(ValueError,match='world_mismatch'):
            service.dispatch('inspect_target',{'request_id':'wrong','world':'other','x':1,'y':2})
        assert service.store.list_jobs() == []
    finally: service.close()


@pytest.mark.parametrize('method,extra',[
    ('replenish',{'request_id':'r'}), ('replenish_reconcile',{'command_id':'c'})])
def test_replenish_uses_api_service_without_binding_desktop(tmp_path,monkeypatch,method,extra):
    from dataclasses import replace
    from sgz_agent import replenish
    service=make_service(tmp_path)
    service.config=replace(service.config,identity={'world':'w'})
    called=[]
    class Refill:
        def __init__(self,config,*,client): called.append(client.stop_event is service.stop_event)
        def run(self,**params): called.append(params);return {'status':'complete'}
        reconcile=run
    monkeypatch.setattr(replenish,'Replenisher',Refill)
    try:
        with pytest.raises(ValueError,match='world_mismatch'):
            service.dispatch(method,{'world':'wrong',**extra})
        assert called==[]
        assert service.dispatch(method,{'world':'w',**extra})['status']=='complete'
        assert called==[True,{'world':'w',**extra}]
        assert service.controller is None
    finally: service.close()


def test_refill_excludes_other_jobs_and_hunt_start_without_blocking_shutdown(tmp_path,monkeypatch):
    from dataclasses import replace
    from sgz_agent import replenish
    service=make_service(tmp_path);service.config=replace(service.config,identity={'world':'w'})
    entered=threading.Event();release=threading.Event();errors=[]
    class Refill:
        def __init__(self,*args,**kwargs):pass
        def run(self,**params): entered.set();assert release.wait(3);return {}
    monkeypatch.setattr(replenish,'Replenisher',Refill)
    def run():
        try:service.dispatch('replenish',{'world':'w','request_id':'r'})
        except Exception as e:errors.append(e)
    worker=threading.Thread(target=run);worker.start();assert entered.wait(1)
    try:
        for method,params in [('prepare_login',{'request_id':'login'}),
                              ('hunt_start',{'world':'w','request_id':'hunt'}),
                              ('observe_map',{}),('replenish',{'world':'w','request_id':'second'})]:
            with pytest.raises(RuntimeError,match='replenish_busy'):service.dispatch(method,params)
        service.request_stop();assert service.stop_event.is_set()
    finally:release.set();worker.join(2);service.close()
    assert errors==[]


def test_exploration_resumes_only_unverified_targets_and_saves_matching_evidence(tmp_path):
    from dataclasses import replace
    from PIL import Image
    from sgz_agent.recognition import ScreenState
    service=make_service(tmp_path)
    service.config=replace(service.config,identity={'world':'expected'})
    job=service.store.create_job('explore','resume',{'world':'expected','points':[[1,2],[2,2]]})
    service.store.save_observation(job['id'],'expected','surface',1,2,{},'prior.png')
    seen=[]
    class Nav:
        ui=SimpleNamespace(activate=lambda:seen.append('activate'))
        def verify_identity(self): seen.append('identity')
        def inspect(self, point):
            seen.append(point)
            return SimpleNamespace(image=Image.new('RGB',(20,20))),ScreenState('details',coordinate=point,fields={'name':'空地'})
    service._navigator=lambda:Nav()
    service._explore(job)
    assert seen == ['activate','identity',(2,2)]
    assert service.store.get_job(job['id'])['status']=='complete'
    records=service.store.observations(job['id'])
    assert len(records)==2
    from pathlib import Path
    assert Path(records[-1]['evidence']).is_file()
    service.close()
