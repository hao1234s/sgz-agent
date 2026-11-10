import math

from .models import Observation, WindowSnapshot


class GuardError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ActionGuard:
    def __init__(self, max_age: float = 2.0):
        self.max_age = max_age

    def check(self, observed: Observation, current: WindowSnapshot,
              now: float, point: tuple[int, int] | None = None) -> None:
        old = observed.window
        if (old.hwnd, old.pid, old.exe.replace("\\", "/").casefold()) != (
                current.hwnd, current.pid, current.exe.replace("\\", "/").casefold()):
            raise GuardError("window_changed")
        if current.minimized:
            raise GuardError("minimized")
        if not current.foreground:
            raise GuardError("focus_lost")
        if old.rect != current.rect:
            raise GuardError("geometry_changed")
        age = now - observed.captured_at
        if not math.isfinite(age) or not 0 <= age <= self.max_age:
            raise GuardError("stale_observation")
        _, _, width, height = current.rect
        if width <= 0 or height <= 0:
            raise GuardError("invalid_geometry")
        if point is not None and (len(point) != 2 or
                any(type(v) is not int for v in point) or
                not (0 <= point[0] < width and 0 <= point[1] < height)):
            raise GuardError("invalid_point")
