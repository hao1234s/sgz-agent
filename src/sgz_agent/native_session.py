"""Shared, bounded teardown for the two native read-only bridges."""
import json
from pathlib import Path
import threading
import time


_retained_sessions = []


def retained_quarantine(config):
    directory = str(getattr(config, 'data_dir', None))
    if any(item[0] == directory for item in _retained_sessions):
        return {'active':True,'incident_id':'native_quiescence_unconfirmed'}
    return None


def drain_native_script(script, *, timeout=3):
    # Frida 17.24's synchronous Python RPC waits on a Condition without observing
    # Cancellable. A late reply only ends this daemon; it cannot unload a script.
    completed = threading.Event()
    outcome = {}
    def request_close():
        try: outcome['state'] = script.exports_sync.close()
        except Exception as error: outcome['error'] = error
        finally: completed.set()
    threading.Thread(target=request_close,name='sgz-native-drain',daemon=True).start()
    if not completed.wait(timeout):raise RuntimeError('native_quiescence_rpc_timeout')
    if 'error' in outcome:raise outcome['error']
    state = outcome['state']
    if (not isinstance(state,dict) or state.get('closed') is not True
            or any(state.get(k) != 0 for k in ('active_native','callbacks','hooks'))):
        raise RuntimeError('native_quiescence_unconfirmed:'+json.dumps(state,ensure_ascii=True))
    return state


def close_native_session(config, session, script, *, timeout=3):
    import frida
    if session is None or getattr(session,'is_detached',False):return
    def bounded(action):
        token = frida.Cancellable()
        timer = threading.Timer(timeout,token.cancel)
        timer.daemon = True; timer.start()
        try:
            with token: return action()
        finally: timer.cancel()
    state,stage = None,'drain'
    try:
        if script is not None:
            state=drain_native_script(script,timeout=timeout)
            stage='unload';bounded(script.unload)
        stage='detach';bounded(session.detach)
    except Exception as error:
        if getattr(session,'is_detached',False):return
        directory=getattr(config,'data_dir',None)
        _retained_sessions.append((str(directory),session,script))
        if directory is not None:
            marker=Path(directory)/'hunt-client-quarantine.json'
            try:
                try:previous=json.loads(marker.read_text(encoding='utf-8'))
                except (OSError,ValueError):previous={}
                if not isinstance(previous,dict):previous={}
                marker.parent.mkdir(parents=True,exist_ok=True)
                temporary=marker.with_suffix('.pending')
                temporary.write_text(json.dumps({**previous,
                    'active':True,'incident_id':previous.get('incident_id','native_quiescence_unconfirmed'),
                    'recorded_at':time.time(),'status':'cleanup_failed','cleanup_stage':stage,
                    'reason':str(error),'cleanup_state':state,
                    'automatic_recovery_allowed':False}),encoding='utf-8')
                temporary.replace(marker)
            except OSError:pass  # The retained reference blocks reconnects in memory.
        raise RuntimeError(f'native_quiescence_unconfirmed:{stage}:session_retained') from error
