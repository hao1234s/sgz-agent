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
