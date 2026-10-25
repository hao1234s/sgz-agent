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
