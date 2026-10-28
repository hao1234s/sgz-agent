from dataclasses import replace

import pytest


def snapshot():
    from sgz_agent.models import WindowSnapshot
    return WindowSnapshot(42, 123, "E:/game/S3Client.exe", (100, 200, 800, 600), True, False)


@pytest.mark.parametrize("change,code", [
    ({"hwnd": 43}, "window_changed"),
    ({"pid": 124}, "window_changed"),
    ({"exe": "E:/other.exe"}, "window_changed"),
    ({"rect": (110, 200, 800, 600)}, "geometry_changed"),
    ({"rect": (100, 200, 900, 600)}, "geometry_changed"),
    ({"foreground": False}, "focus_lost"),
    ({"minimized": True}, "minimized"),
])
def test_rejects_changed_target_before_input(change, code):
    from sgz_agent.guard import ActionGuard, GuardError
    from sgz_agent.models import Observation
    old = snapshot()
    observation = Observation("frame1", old, 100.0)
    with pytest.raises(GuardError) as error:
        ActionGuard().check(observation, replace(old, **change), 101.0, (400, 300))
    assert error.value.code == code


@pytest.mark.parametrize("point", [(-1, 0), (800, 0), (0, 600), (1.5, 2), (True, 2)])
def test_rejects_invalid_client_coordinate(point):
    from sgz_agent.guard import ActionGuard, GuardError
    from sgz_agent.models import Observation
    current = snapshot()
    with pytest.raises(GuardError, match="invalid_point"):
        ActionGuard().check(Observation("f", current, 100), current, 101, point)


def test_rejects_old_frame_even_if_window_unchanged():
    from sgz_agent.guard import ActionGuard, GuardError
    from sgz_agent.models import Observation
    current = snapshot()
    with pytest.raises(GuardError, match="stale_observation"):
        ActionGuard().check(Observation("f", current, 100), current, 102.01, (20, 20))


def test_accepts_current_frame_and_client_edges():
    from sgz_agent.guard import ActionGuard
    from sgz_agent.models import Observation
    current = snapshot()
    ActionGuard().check(Observation("f", current, 100), current, 101, (799, 599))
