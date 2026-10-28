from dataclasses import dataclass
import time


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str = ""
    target: tuple[int, int] | None = None


class LoginFlow:
    def __init__(self, started_at=None):
        self.started_at = time.monotonic() if started_at is None else started_at
        self.refreshes = 0
        self.next_refresh_at = 0
        self.open_attempts = 0
        self.next_open_at = 0

    def next(self, screen, now=None):
        now = time.monotonic() if now is None else now
        if screen.kind == "map":
            return Decision("complete") if screen.identity_matched else Decision("pause", "identity_unverified")
        if now - self.started_at > 300:
            return Decision("pause", "login_timeout")
        if screen.kind in {"qr_ready", "loading"}:
            return Decision("wait", "scan_on_phone" if screen.kind == "qr_ready" else "loading")
        if screen.kind == "qr_expired":
            if now < self.next_refresh_at:
                return Decision("wait", "await_qr_refresh")
            if self.refreshes >= 2:
                return Decision("pause", "qr_refresh_limit")
            self.refreshes += 1
            self.next_refresh_at = now + 30
            return Decision("refresh_qr")
        if screen.kind == "login_entry":
            if now < self.next_open_at:
                return Decision("wait", "await_qr_page")
            if self.open_attempts >= 3:
                return Decision("pause", "login_entry_retry_limit")
            self.open_attempts += 1
            self.next_open_at = now + 30
            return Decision("open_qr")
        if screen.kind == "role_select":
            return Decision("pause", "select_role_on_computer")
        return Decision("pause", "unknown_screen")


def region_points(x0, y0, x1, y1, *, max_points=25):
    if any(type(v) is not int for v in (x0, y0, x1, y1)):
        raise ValueError("integer_coordinates_required")
    if not (0 <= x0 <= x1 <= 99999 and 0 <= y0 <= y1 <= 99999):
        raise ValueError("invalid_region")
    if (x1 - x0 + 1) * (y1 - y0 + 1) > max_points:
        raise ValueError(f"region_limit_{max_points}")
    result = []
    for y in range(y0, y1 + 1):
        xs = range(x0, x1+1) if (y-y0) % 2 == 0 else range(x1, x0-1, -1)
        result.extend((x, y) for x in xs)
    return result


class ExploreFlow:
    def __init__(self, targets):
        self.targets = list(targets)
        self.index = 0
        self._seen = None

    @property
    def complete(self):
        return self.index == len(self.targets)

    def next(self, screen):
        if self.complete:
            return Decision("complete")
        target = self.targets[self.index]
        if screen.kind == "map":
            self._seen = None
            return Decision("navigate", target=target)
        if screen.kind != "details":
            self._seen = None
            return Decision("pause", "unknown_screen")
        if screen.coordinate != target:
            self._seen = None
            return Decision("pause", "coordinate_mismatch")
        if self._seen != target:
            self._seen = target
            return Decision("observe", target=target)
        return Decision("record", target=target)

    def recorded(self, target):
        if self.complete or target != self.targets[self.index]:
            raise ValueError("unexpected_target")
        self.index += 1
        self._seen = None
