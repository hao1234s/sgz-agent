from types import SimpleNamespace
import threading
import pytest


def test_activation_waits_for_foreground_confirmation():
    from sgz_agent.windows import WindowsDesktop
    states = iter([False, False, True])
    desktop = WindowsDesktop.__new__(WindowsDesktop)
    desktop.user = SimpleNamespace(SetForegroundWindow=lambda hwnd: True)
    desktop.snapshot = lambda hwnd: SimpleNamespace(foreground=next(states))
    desktop.activate(123)
    assert list(states) == []


def test_managed_window_prepares_calibrated_size_before_activation():
    from sgz_agent.game_ui import GameUI
    events=[]
    desktop=SimpleNamespace(prepare_window=lambda hwnd,size,stopped:events.append(('prepare',hwnd,size)),
                            activate=lambda hwnd,**kwargs:events.append(('activate',hwnd)))
    ui=GameUI.__new__(GameUI)
    ui.controller=SimpleNamespace(desktop=desktop,hwnd=123,stopped=threading.Event(),park_pointer=lambda:events.append('park'))
    ui.size=(1264,711)
    ui.activate()
    assert events==[('prepare',123,(1264,711)),('activate',123),'park']


def test_activation_uses_only_verified_caption_after_foreground_request_denied():
    from sgz_agent.windows import WindowsDesktop
    desktop=WindowsDesktop.__new__(WindowsDesktop)
    state=SimpleNamespace(foreground=False,pid=1,exe='game.exe',rect=(10,40,100,60))
    desktop.snapshot=lambda hwnd:state
    desktop.user=SimpleNamespace(SetForegroundWindow=lambda hwnd:False)
    desktop._activate_caption=lambda hwnd,stopped=None: setattr(state,'foreground',True)
    desktop.activate(123)
    assert state.foreground


def test_caption_activation_rejects_client_area_and_always_releases_button():
    from sgz_agent.windows import WindowsDesktop
    from sgz_agent.guard import GuardError
    desktop=WindowsDesktop.__new__(WindowsDesktop)
    state=SimpleNamespace(foreground=False,pid=1,exe='game.exe',rect=(10,40,100,60))
    desktop.snapshot=lambda hwnd:state
    desktop._caption_point=lambda hwnd:None
    events=[]
    desktop.move=lambda point:events.append('move')
    desktop.button=lambda down:events.append(down)
    with pytest.raises(GuardError,match='activation_denied'):
        desktop._activate_caption(123)
    assert events==[]
    desktop._caption_point=lambda hwnd:(50,20)
    desktop.ensure_hit=lambda hwnd,point:None
    def button(down):
        events.append(down)
        if down: raise OSError('input_failed')
    desktop.button=button
    with pytest.raises(OSError,match='input_failed'):
        desktop._activate_caption(123)
    assert events==['move',True,False]


def test_managed_window_stop_prevents_geometry_and_activation_changes():
    from sgz_agent.windows import WindowsDesktop
    from sgz_agent.guard import GuardError
    desktop=WindowsDesktop.__new__(WindowsDesktop)
    stopped=threading.Event(); stopped.set()
    with pytest.raises(GuardError,match='stopped'):
        desktop.prepare_window(123,(1264,711),stopped)


def test_stop_during_caption_probe_prevents_pointer_motion():
    from sgz_agent.windows import WindowsDesktop
    from sgz_agent.guard import GuardError
    desktop=WindowsDesktop.__new__(WindowsDesktop)
    stopped=threading.Event()
    desktop.snapshot=lambda hwnd:SimpleNamespace(pid=1,exe='game.exe',rect=(10,40,100,60))
    def probe(hwnd):
        stopped.set()
        return (50,20)
    desktop._caption_point=probe
    events=[]
    desktop.move=lambda point:events.append(point)
    with pytest.raises(GuardError,match='stopped'):
        desktop._activate_caption(123,stopped)
    assert events==[]
