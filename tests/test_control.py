from dataclasses import replace
from PIL import Image
import pytest

from sgz_agent.models import WindowSnapshot


class FakeDesktop:
    def __init__(self):
        self.window = WindowSnapshot(42, 123, "E:/game.exe", (100, 200, 800, 600), True, False)
        self.events = []
        self.on_down = lambda: None
        self.fail_move = False
        self.on_key = lambda key, down: None

    def snapshot(self, hwnd):
        return self.window

    def capture(self, window):
        return Image.new("RGB", (800, 600))

    def ensure_hit(self, hwnd, point):
        pass

    def move(self, point):
        if self.fail_move:
            raise OSError("move failed")
        self.events.append(("move", point))

    def button(self, down):
        self.events.append(("button", down))
        if down:
            self.on_down()

    def key(self, key, down):
        self.events.append(('key', key, down))
        self.on_key(key, down)


def test_stop_prevents_any_new_input():
    from sgz_agent.control import Controller
    desktop = FakeDesktop()
    controller = Controller(desktop, 42)
    frame = controller.observe()
    controller.stop()
    with pytest.raises(RuntimeError, match="stopped"):
        controller.click(frame, 50, 50)
    assert desktop.events == []


def test_stop_during_drag_releases_mouse_without_more_motion():
    from sgz_agent.control import Controller
    desktop = FakeDesktop()
    controller = Controller(desktop, 42)
    desktop.on_down = controller.stop
    with pytest.raises(RuntimeError, match="stopped"):
        controller.drag(controller.observe(), (10, 20), (50, 60), duration=0.01)
    assert desktop.events == [("move", (110, 220)), ("button", True), ("button", False)]


def test_failure_during_drag_releases_owned_button():
    from sgz_agent.control import Controller
    desktop = FakeDesktop()
    controller = Controller(desktop, 42)
    desktop.on_down = lambda: setattr(desktop, "fail_move", True)
    with pytest.raises(OSError, match="move failed"):
        controller.drag(controller.observe(), (10, 20), (50, 60), duration=0.01)
    assert desktop.events[-1] == ("button", False)


def test_focus_loss_between_capture_and_click_prevents_input():
    from sgz_agent.control import Controller
    desktop = FakeDesktop()
    controller = Controller(desktop, 42)
    frame = controller.observe()
    desktop.window = replace(desktop.window, foreground=False)
    with pytest.raises(RuntimeError, match="focus_lost"):
        controller.click(frame, 10, 10)
    assert desktop.events == []


def test_client_coordinate_is_converted_once_and_click_is_released():
    from sgz_agent.control import Controller
    desktop = FakeDesktop()
    controller = Controller(desktop, 42)
    controller.click(controller.observe(), 20, 30)
    assert desktop.events == [("move", (120, 230)), ("button", True), ("button", False)]


@pytest.mark.parametrize('action', ['click', 'drag'])
def test_ui_transition_with_unchanged_window_prevents_input(action):
    from sgz_agent.control import Controller
    desktop = FakeDesktop()
    controller = Controller(desktop, 42)
    frame = controller.observe()
    # A small dialog replaces only part of the screen; window metadata is unchanged.
    changed = frame.image.copy()
    changed.putpixel((30, 40), (255, 255, 255))
    desktop.capture = lambda window: changed
    with pytest.raises(RuntimeError, match='screen_changed'):
        if action == 'click':
            controller.click(frame, 20, 30)
        else:
            controller.drag(frame, (20, 30), (40, 50))
    assert desktop.events == []


def test_calibrated_regions_allow_background_animation_but_check_button_and_context():
    from sgz_agent.control import Controller
    desktop = FakeDesktop()
    controller = Controller(desktop, 42)
    frame = controller.observe()
    regions = [{'box': [10, 10, 40, 40], 'max_delta': 20},
               {'box': [100, 100, 200, 130], 'max_delta': 20}]
    animated = frame.image.copy()
    animated.putpixel((500, 500), (255, 0, 0))
    desktop.capture = lambda window: animated
    controller.click(frame, 20, 30, regions=regions)
    assert desktop.events[-2:] == [('button', True), ('button', False)]
    for point in [(20, 30), (110, 110)]:
        desktop.events.clear()
        changed = animated.copy()
        changed.putpixel(point, (255, 255, 255))
        desktop.capture = lambda window: changed
        with pytest.raises(RuntimeError, match='screen_changed'):
            controller.click(frame, 20, 30, regions=regions)
        assert desktop.events == []


@pytest.mark.parametrize('regions', [[], [{'box': [0,0,5,5]}],
    [{'box': [0,0,5,5]}, {'box': [10,10,40,40]}],
    [{'box': [10,10,40,40], 'max_delta': 255}, {'box': [100,100,200,130]}]])
def test_calibrated_guard_rejects_missing_target_proof_or_excessive_tolerance(regions):
    from sgz_agent.control import Controller
    desktop = FakeDesktop()
    controller = Controller(desktop, 42)
    with pytest.raises(RuntimeError, match='invalid_visual_regions'):
        controller.click(controller.observe(), 20, 30, regions=regions)
    assert desktop.events == []


def test_coordinate_entry_is_click_focus_select_all_and_digits_only():
    from sgz_agent.control import Controller
    desktop = FakeDesktop()
    controller = Controller(desktop, 42)
    controller.enter_coordinate(controller.observe(), 20, 30, '830')
    assert desktop.events[:3] == [('move',(120,230)),('button',True),('button',False)]
    assert desktop.events[3:] == [('key',17,True),('key',65,True),('key',65,False),('key',17,False),
                                 ('key',56,True),('key',56,False),('key',51,True),('key',51,False),
                                 ('key',48,True),('key',48,False)]
    desktop.events.clear()
    with pytest.raises(ValueError, match='coordinate_digits_required'):
        controller.enter_coordinate(controller.observe(),20,30,'830\n')
    assert desktop.events == []


def test_stop_during_coordinate_typing_releases_owned_modifier():
    from sgz_agent.control import Controller
    desktop = FakeDesktop()
    controller = Controller(desktop, 42)
    desktop.on_key = lambda key, down: controller.stop() if down else None
    with pytest.raises(RuntimeError, match='stopped'):
        controller.enter_coordinate(controller.observe(),20,30,'830')
    assert desktop.events[-2:] == [('key',17,True),('key',17,False)]


def test_failed_key_release_still_releases_other_keys_and_close_retries_owned_key():
    from sgz_agent.control import Controller
    desktop=FakeDesktop();controller=Controller(desktop,42)
    failures=[2]
    def reject_a_release(key,down):
        if key==65 and not down and failures[0]:
            failures[0]-=1
            raise OSError('key_up_rejected')
    desktop.on_key=reject_a_release
    with pytest.raises(RuntimeError,match='key_release_failed'):
        controller.enter_coordinate(controller.observe(),20,30,'830')
    assert ('key',17,False) in desktop.events
    assert not any(event[0]=='key' and event[1]==56 for event in desktop.events)
    controller.close()
    assert desktop.events[-1]==('key',65,False)
    assert not controller._held_keys
