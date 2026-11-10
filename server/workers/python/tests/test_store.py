import json
import pytest


def test_duplicate_request_survives_restart_without_creating_new_job(tmp_path):
    from sgz_agent.store import Store
    store = Store(tmp_path)
    first = store.create_job("explore", "request1", {"x": 12})
    store.close()
    store = Store(tmp_path)
    assert store.create_job("explore", "request1", {"x": 12})["id"] == first["id"]
    with pytest.raises(ValueError, match="request_conflict"):
        store.create_job("explore", "request1", {"x": 13})
    store.close()


def test_worlds_are_isolated_and_type_changes_preserve_history(tmp_path):
    from sgz_agent.store import Store
    store = Store(tmp_path)
    job = store.create_job("explore", "r", {})
    for world, category in [("season1", "unknown"), ("season1", "wood"), ("season2", "stone")]:
        store.save_observation(job["id"], world, "main", 12, 34, {"type": category}, "evidence.png")
    assert store.target_count() == 2
    records = store.observations(job["id"])
    assert [r["fields"]["type"] for r in records] == ["unknown", "wood", "stone"]
    output = store.export(job["id"], "json")
    assert len(json.loads(output.read_text("utf-8"))["observations"]) == 3
    store.close()


def test_running_job_recovers_paused_without_auto_execution(tmp_path):
    from sgz_agent.store import Store
    store = Store(tmp_path)
    job = store.create_job("login", "r", {})
    store.update_job(job["id"], "running")
    store.close()
    store = Store(tmp_path)
    store.recover_jobs()
    restored = store.get_job(job["id"])
    assert restored["status"] == "paused"
    assert restored["reason"] == "interrupted"
    store.close()
