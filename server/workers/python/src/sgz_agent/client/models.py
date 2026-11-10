from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WindowSnapshot:
    hwnd: int
    pid: int
    exe: str
    rect: tuple[int, int, int, int]
    foreground: bool
    minimized: bool
    title: str = ""


@dataclass(frozen=True)
class Observation:
    id: str
    window: WindowSnapshot
    captured_at: float


@dataclass(frozen=True)
class Frame:
    observation: Observation
    image: Any


@dataclass(frozen=True)
class TextBox:
    text: str
    score: float
    box: tuple[float, float, float, float]
