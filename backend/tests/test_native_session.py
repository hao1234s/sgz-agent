import threading
import time
from types import SimpleNamespace

import pytest

from sgz_agent.native_session import close_native_session, retained_quarantine


def test_stuck_rpc_returns_with_deadline_without_unloading_active_session(tmp_path):
    release = threading.Event()
    calls=[]
    def stuck_close():
        release.wait(2)
        return dict(closed=True,active_native=0,callbacks=0,hooks=0)
    script=SimpleNamespace(exports_sync=SimpleNamespace(close=stuck_close),
                           unload=lambda:calls.append('unload'))
    session=SimpleNamespace(is_detached=False,detach=lambda:calls.append('detach'))
    config=SimpleNamespace(data_dir=tmp_path)
    started=time.monotonic()
    try:
        with pytest.raises(RuntimeError,match='quiescence'):
            close_native_session(config,session,script,timeout=.02)
        assert time.monotonic()-started<1
        assert calls==[]
        assert retained_quarantine(config)['active'] is True
    finally:release.set()


def test_failed_unload_does_not_force_detach_and_keeps_diagnostic_state(tmp_path):
    import json
    calls=[]
    def unload():
        calls.append('unload')
        raise RuntimeError('unload did not finish')
    script=SimpleNamespace(exports_sync=SimpleNamespace(close=lambda:dict(
        closed=True,active_native=0,callbacks=0,hooks=0)),unload=unload)
    session=SimpleNamespace(is_detached=False,detach=lambda:calls.append('detach'))
    config=SimpleNamespace(data_dir=tmp_path)
    with pytest.raises(RuntimeError,match='session_retained'):
        close_native_session(config,session,script)
    assert calls==['unload']
    incident=json.loads((tmp_path/'hunt-client-quarantine.json').read_text(encoding='utf-8'))
    assert incident['cleanup_stage']=='unload'
    assert incident['cleanup_state']['closed'] is True
