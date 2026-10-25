import threading
import time
import uuid

from .guard import ActionGuard, GuardError
from .models import Frame, Observation


class Controller:
    def __init__(self, desktop, hwnd: int, clock=time.monotonic):
        self.desktop, self.hwnd, self.clock = desktop, hwnd, clock
        self.guard = ActionGuard()
        self.stopped = threading.Event()
        self._lock = threading.RLock()
        self._held = False

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

    def click(self, frame, x, y):
        with self._lock:
            try:
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

    def stop(self):
        self.stopped.set()

    def close(self):
        self.stop()
        with self._lock:
            self._release()
