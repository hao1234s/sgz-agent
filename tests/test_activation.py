from types import SimpleNamespace


def test_activation_waits_for_foreground_confirmation():
    from sgz_agent.windows import WindowsDesktop
    states = iter([False, False, True])
    desktop = WindowsDesktop.__new__(WindowsDesktop)
    desktop.user = SimpleNamespace(SetForegroundWindow=lambda hwnd: True)
    desktop.snapshot = lambda hwnd: SimpleNamespace(foreground=next(states))
    desktop.activate(123)
    assert list(states) == []
