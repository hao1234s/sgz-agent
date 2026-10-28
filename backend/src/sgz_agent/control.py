import threading
import time
import uuid
import re
from PIL import ImageChops

from .guard import ActionGuard, GuardError
from .models import Frame, Observation
from .visual_guard import check_regions


class Controller:
    def __init__(self, desktop, hwnd: int, clock=time.monotonic):
        self.desktop, self.hwnd, self.clock = desktop, hwnd, clock
        self.guard = ActionGuard()
        self.stopped = threading.Event()
        self._lock = threading.RLock()
        self._held = False
        self._held_keys = []

    def _check(self, frame, point=None):
        if self.stopped.is_set():
            raise GuardError("stopped")
        current = self.desktop.snapshot(self.hwnd)
        self.guard.check(frame.observation, current, self.clock(), point)
        if point is not None:
            self.desktop.ensure_hit(self.hwnd, (current.rect[0] + point[0], current.rect[1] + point[1]))
        return current

    def observe(self):
        with self._lock:
            window = self.desktop.snapshot(self.hwnd)
            obs = Observation(uuid.uuid4().hex, window, self.clock())
            self.guard.check(obs, window, self.clock())
            image = self.desktop.capture(window)
            self.guard.check(obs, self.desktop.snapshot(self.hwnd), self.clock())
            return Frame(obs, image)

    def _move(self, frame, point):
        current = self._check(frame, point)
        self.desktop.move((current.rect[0] + point[0], current.rect[1] + point[1]))

    def _release(self):
        if self._held:
            self.desktop.button(False)
            self._held = False

    def _release_keys(self):
        errors = []
        for key in reversed(self._held_keys.copy()):
            try:
                self.desktop.key(key, False)
            except Exception as error:
                errors.append(error)
            else:
                self._held_keys.remove(key)
        if errors:
            raise RuntimeError('key_release_failed') from errors[0]

    def _check_screen(self, frame, regions=None, point=None):
        self._check(frame)
        fresh = self.observe()
        self._check(frame)
        if regions is not None:
            check_regions(frame.image, fresh.image, regions, point)
        elif frame.image.size != fresh.image.size or ImageChops.difference(
                frame.image.convert('RGB'), fresh.image.convert('RGB')).getbbox() is not None:
            raise GuardError('screen_changed')

    def click(self, frame, x, y, *, regions=None):
        with self._lock:
            try:
                self._check_screen(frame, regions, (x, y))
                self._move(frame, (x, y))
                self._check(frame, (x, y))
                self._held = True
                self.desktop.button(True)
            finally:
                self._release()

    def drag(self, frame, start, end, duration=0.3):
        if not 0 < duration <= 1:
            raise ValueError("drag duration must be in (0, 1]")
        with self._lock:
            self._check(frame, end)
            try:
                self._check_screen(frame)
                self._move(frame, start)
                self._check(frame, start)
                self._held = True
                self.desktop.button(True)
                for step in range(1, 11):
                    point = tuple(round(a + (b - a) * step / 10) for a, b in zip(start, end))
                    self._move(frame, point)
                    self.stopped.wait(duration / 10)
                self._check(frame, end)
            finally:
                self._release()

    def scroll(self, frame, x, y, delta, *, regions=None):
        if type(delta) is not int or delta == 0 or abs(delta) > 1200 or delta % 120:
            raise ValueError('invalid_scroll_delta')
        with self._lock:
            self._check_screen(frame, regions, (x,y))
            self._move(frame, (x,y))
            self._check(frame, (x,y))
            self.desktop.scroll(delta)

    def park_pointer(self):
        """Keep the game's rendered cursor out of calibrated text regions."""
        with self._lock:
            if self.stopped.is_set():
                raise GuardError('stopped')
            current = self.desktop.snapshot(self.hwnd)
            observation = Observation(uuid.uuid4().hex, current, self.clock())
            point = self.desktop.caption_point(self.hwnd)
            self.guard.check(observation, self.desktop.snapshot(self.hwnd), self.clock())
            if point is None:
                raise GuardError('caption_unavailable')
            if self.stopped.is_set():
                raise GuardError('stopped')
            self.desktop.ensure_hit(self.hwnd, point)
            self.desktop.move(point)

    def enter_coordinate(self, frame, x, y, value, *, regions=None):
        if not isinstance(value, str) or re.fullmatch(r'[0-9]{1,5}', value) is None:
            raise ValueError('coordinate_digits_required')
        with self._lock:
            if self._held_keys:
                raise GuardError('keys_still_held')
            self.click(frame, x, y, regions=regions)
            self.park_pointer()
            if self.stopped.wait(.15):
                raise GuardError('stopped')
            focused = self.observe()
            def down(key):
                self._check(focused, (x,y))
                self._held_keys.append(key)
                self.desktop.key(key, True)
            def up(key):
                self.desktop.key(key, False)
                self._held_keys.remove(key)
            try:
                self._check_screen(focused, regions, (x,y))
                down(17)
                down(65)
                up(65)
                up(17)
                for digit in value:
                    down(ord(digit))
                    up(ord(digit))
            finally:
                self._release_keys()

    def stop(self):
        self.stopped.set()

    def close(self):
        self.stop()
        with self._lock:
            try:
                self._release()
            finally:
                self._release_keys()
