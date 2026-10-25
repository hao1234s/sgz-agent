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


def close_native_session(config, session, script, *, timeout=3):
    import frida
    if session is None or getattr(session, 'is_detached', False):
        return
    def bounded(action):
        token = frida.Cancellable()
        timer = threading.Timer(timeout,token.cancel)
        timer.daemon = True; timer.start()
        try:
            with token: return action()
        finally: timer.cancel()
    if script is not None:
        state = None
        try:
            # Frida 17.24's synchronous Python RPC waits on a Condition without
            # observing Cancellable. Bound our wait independently; a late reply
            # only ends this daemon, never unloads or resumes a quarantined session.
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
                raise RuntimeError('native_quiescence_unconfirmed')
        except Exception as error:
            if getattr(session,'is_detached',False):return
            directory = getattr(config,'data_dir',None)
            _retained_sessions.append((str(directory),session,script))
            if directory is not None:
                marker = Path(directory)/'hunt-client-quarantine.json'
                try:
                    marker.parent.mkdir(parents=True,exist_ok=True)
                    temporary = marker.with_suffix('.pending')
                    temporary.write_text(json.dumps({
                        'active':True,'incident_id':'native_quiescence_unconfirmed',
                        'recorded_at':time.time(),'status':'cleanup_failed',
                        'reason':str(error),'cleanup_state':state,
                        'automatic_recovery_allowed':False}),encoding='utf-8')
                    temporary.replace(marker)
                except OSError:
                    pass  # The retained reference also blocks reconnects in memory.
            raise RuntimeError('native_quiescence_unconfirmed:session_retained') from error
        try:
            bounded(script.unload)
        finally:
            if not getattr(session,'is_detached',False):bounded(session.detach)
    else:
        bounded(session.detach)
