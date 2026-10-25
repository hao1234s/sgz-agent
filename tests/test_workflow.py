import pytest
from sgz_agent.recognition import ScreenState


def test_qr_disappearance_does_not_complete_login():
    from sgz_agent.workflow import LoginFlow
    flow = LoginFlow(started_at=0)
    assert flow.next(ScreenState("qr_ready"), 1).action == "wait"
    assert flow.next(ScreenState("loading"), 2).action == "wait"
    assert flow.next(ScreenState("unknown"), 3).action == "pause"
    assert flow.next(ScreenState("map"), 4).action == "pause"


def test_already_logged_in_requires_verified_identity():
    from sgz_agent.workflow import LoginFlow
    assert LoginFlow(0).next(ScreenState("map", identity_matched=True), 1).action == "complete"


def test_qr_refresh_is_bounded_and_wait_can_expire():
    from sgz_agent.workflow import LoginFlow
    flow = LoginFlow(0)
    assert flow.next(ScreenState("qr_expired"), 1).action == "refresh_qr"
    assert flow.next(ScreenState("qr_expired"), 2).action == "wait"
    assert flow.next(ScreenState("qr_expired"), 31).action == "refresh_qr"
    assert flow.next(ScreenState("qr_expired"), 32).action == "wait"
    assert flow.next(ScreenState("qr_expired"), 61).action == "pause"
    assert LoginFlow(0).next(ScreenState("qr_ready"), 301).action == "pause"


def test_region_has_bounded_serpentine_order():
    from sgz_agent.workflow import region_points
    assert region_points(10, 20, 12, 21) == [(10,20),(11,20),(12,20),(12,21),(11,21),(10,21)]
    with pytest.raises(ValueError):
        region_points(0,0,5,5)
    with pytest.raises(ValueError):
        region_points(-1,0,1,1)


def test_exploration_records_only_after_two_matching_coordinate_reads():
    from sgz_agent.workflow import ExploreFlow
    flow = ExploreFlow([(12,34)])
    assert flow.next(ScreenState("map")).action == "navigate"
    assert flow.next(ScreenState("details", coordinate=(12,35))).action == "pause"
    assert flow.next(ScreenState("details", coordinate=(12,34))).action == "observe"
    assert flow.next(ScreenState("details", coordinate=(12,34))).action == "record"
    assert not flow.complete
    flow.recorded((12,34))
    assert flow.complete


def test_login_entry_waits_for_transition_and_has_two_bounded_retries():
    from sgz_agent.workflow import LoginFlow
    flow = LoginFlow(0)
    screen = ScreenState('login_entry')
    assert flow.next(screen, 0).action == 'open_qr'
    assert flow.next(screen, 2).action == 'wait'
    assert flow.next(screen, 30).action == 'open_qr'
    assert flow.next(screen, 32).action == 'wait'
    assert flow.next(screen, 60).action == 'open_qr'
    assert flow.next(screen, 90).action == 'pause'
