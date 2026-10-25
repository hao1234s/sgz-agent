"""One explicitly authorized diagnostic attach; no gameplay mutation or quarantine reset."""
import argparse
import json
from pathlib import Path
import threading
import time

import frida
from sgz_agent import hunt_client
from sgz_agent.client_api import _fingerprint, SUPPORTED_SHA256, _bridge_lock
from sgz_agent.config import load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-controlled-session', action='store_true')
    args = parser.parse_args()
    if not args.approved_controlled_session:
        parser.error('This diagnostic requires separate approval for one controlled session.')
    config = load_config('config/game.local.json')
    stat = config.executable.stat()
    if _fingerprint(str(config.executable), stat.st_size, stat.st_mtime_ns) != SUPPORTED_SHA256:
        raise RuntimeError('unverified_client_hash')
    identity = config.identity
    if not all(identity.get(k) for k in ('role_id','region','api_server_id','api_season','world')):
        raise RuntimeError('identity_binding_missing')
    output = config.data_dir / 'network-probe' / ('lifecycle-check-' + time.strftime('%Y%m%d-%H%M%S'))
    output.mkdir(parents=True,exist_ok=False)
    journal = (output/'session.jsonl').open('w',encoding='utf-8')
    def record(kind, **data):
        row={'kind':kind,'epoch':time.time(),**data}
        journal.write(json.dumps(row,ensure_ascii=True)+'\n');journal.flush()
        print(json.dumps(row,ensure_ascii=True),flush=True)
    device = frida.get_local_device()
    matches = [p for p in device.enumerate_processes() if p.name.lower()==config.executable.name.lower()]
    if len(matches)!=1: raise RuntimeError('single_client_required')
    session = None
    current_script = None
    safe_to_detach = True
    detached = threading.Event()
    sequence = 0
    deadline = time.monotonic()+180
    source_dir = Path(hunt_client.__file__).parent
    helper = (source_dir/'native_lifecycle.js').read_text(encoding='utf-8')
    bridge = (source_dir/'hunt_client.js').read_text(encoding='utf-8')
    if not _bridge_lock.acquire(blocking=False):raise RuntimeError('api_bridge_busy')
    def read(operation, **params):
        nonlocal current_script, safe_to_detach, sequence
        if sequence>=8 or time.monotonic()>=deadline:raise RuntimeError('diagnostic_budget_exhausted')
        sequence+=1
        options={'path':str(config.executable).replace('\\','/').lower(),
                 'role_id':str(identity['role_id']),'region':identity['region'],
                 'server_id':identity['api_server_id'],'season':identity['api_season'],
                 'operation':operation,**params}
        complete=threading.Event(); result={}; errors=[]
        def receive(message,data):
            if message['type']=='error':errors.append(message.get('description','script_error'));complete.set()
            elif message['type']=='send':
                payload=message['payload']
                if payload.get('kind')=='complete':result.update(payload);complete.set()
                elif payload.get('kind')=='failure':errors.append(payload.get('reason'));complete.set()
        record('operation_begin',sequence=sequence,operation=operation)
        current_script=session.create_script('const OPTIONS='+json.dumps(options)+';\n'+helper+'\n'+bridge)
        current_script.on('message',receive)
        safe_to_detach=False
        try:
            current_script.load()
            if not complete.wait(20):raise RuntimeError('diagnostic_operation_timeout')
            if errors:raise RuntimeError(errors[0])
            (output/f'{sequence:03d}-{operation}.json').write_text(json.dumps(result,ensure_ascii=True,indent=2),encoding='utf-8')
            return result['data']
        finally:
            if not detached.is_set():
                token=frida.Cancellable(); timer=threading.Timer(5,token.cancel); timer.daemon=True;timer.start()
                try:
                    with token: state=current_script.exports_sync.close()
                finally:timer.cancel()
                record('cleanup_state',sequence=sequence,state=state)
                if state.get('closed') is not True or any(state.get(k)!=0 for k in ('active_native','callbacks','hooks')):
                    raise RuntimeError('diagnostic_quiescence_unconfirmed')
                record('quiescent_confirmed',sequence=sequence,state=state)
                current_script.unload();current_script=None;safe_to_detach=True
                record('operation_unloaded',sequence=sequence)
    try:
        record('attach_begin',pid=matches[0].pid,attempt=1)
        session=device.attach(matches[0].pid)
        session.on('detached',lambda *a:detached.set())
        record('attach_returned')
        roster=read('roster')
        ready=[]
        for raw in roster.get('armies',[]):
            army=hunt_client.normalize_army(raw)
            if army['kind']=='regular' and army['formed'] and army['phase'] in hunt_client.READY_PHASES:
                if len(ready)>=3:break
                data=read('ready',army_id=int(army['id']));ready.append(data)
        read('bases')
        if ready:
            raw=ready[0]['army'];position=hunt_client.normalize_army(raw)['position']
            # Known giant coordinate is a route preview only, never a dispatch.
            if position:
                read('preview',request=hunt_client.preview_request(str(raw['army_id']),[579,822]))
        record('checks_complete',operations=sequence,gameplay_commands=0)
    except Exception as error:
        record('failed',reason=f'{type(error).__name__}:{error}')
        raise
    finally:
        if session and not detached.is_set():
            if safe_to_detach:
                session.detach();record('session_detached')
            else:
                # Do not unload a possibly active native callback on process exit.
                record('cleanup_unconfirmed_process_retained',action='waiting_for_client_exit')
                while not detached.wait(1):pass
        _bridge_lock.release();journal.close()


if __name__=='__main__':
    main()
