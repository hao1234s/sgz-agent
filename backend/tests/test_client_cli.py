from types import SimpleNamespace

import pytest


def test_prepare_client_stop_after_activation_prevents_motion_and_success(monkeypatch, capsys, tmp_path):
    from sgz_agent import cli, windows
    from sgz_agent.guard import GuardError
    callbacks, events = [], []
    config = SimpleNamespace(executable=tmp_path/'game.exe', window_title_pattern=None,
                             ui={'client_size':[1264,711]})
    class Listener:
        def __init__(self, callback): callbacks.append(callback)
        def start(self): events.append('listen')
        def close(self): events.append('listener_closed')
    desktop = SimpleNamespace(list_windows=lambda exe:[], acquire=lambda hwnd:events.append('acquire'),
        prepare_window=lambda *args:None, activate=lambda *args:callbacks[0](),
        release=lambda:events.append('released'),
        snapshot=lambda hwnd:pytest.fail('stopped controller must not continue observing'),
        move=lambda point:pytest.fail('stopped controller must not move'))
    monkeypatch.setattr(cli, 'load_config', lambda path:config)
    monkeypatch.setattr(cli, 'select_window', lambda *args:SimpleNamespace(hwnd=42))
    monkeypatch.setattr(windows, 'WindowsDesktop', lambda:desktop)
    monkeypatch.setattr(windows, 'StopListener', Listener)
    monkeypatch.setattr('sys.argv', ['sgz-agent','prepare-client'])
    with pytest.raises(GuardError, match='stopped'):
        cli.main()
    assert events == ['acquire','listen','listener_closed','released']
    assert 'client_ready' not in capsys.readouterr().out
