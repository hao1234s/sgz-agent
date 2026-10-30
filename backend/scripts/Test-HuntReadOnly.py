"""One explicitly authorized diagnostic attach; no gameplay mutation or quarantine reset."""
import argparse
import json
from pathlib import Path
import threading
import time

import frida
from sgz_agent.client import hunt_client
from sgz_agent.client.client_api import _fingerprint, SUPPORTED_SHA256, _bridge_lock
from sgz_agent.client.config import default_config_path, load_config
from sgz_agent.client.native_session import drain_native_script


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-controlled-session', action='store_true')
    parser.add_argument('--logistics-only',action='store_true',help='Requires approval for logistics parameter inspection; never sends troop commands.')
    parser.add_argument('--observe-replenish',action='store_true',help='Passively observe whitelisted refill packets for up to 110 seconds; sends no gameplay commands.')
    args = parser.parse_args()
    if args.logistics_only and args.observe_replenish:
        parser.error('Select only one diagnostic mode.')
    if not args.approved_controlled_session:
        parser.error('This diagnostic requires separate approval for one controlled session.')
    config = load_config(default_config_path(Path(__file__).resolve().parents[2]))
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
                elif payload.get('kind')=='failure':
                    (output/f'{sequence:03d}-{operation}-failure.json').write_text(json.dumps(payload,ensure_ascii=True,indent=2),encoding='utf-8')
                    errors.append(payload.get('reason'));complete.set()
                elif payload.get('kind') in {'diagnostic_stage','diagnostic_partial'}:
                    record('diagnostic_stage',sequence=sequence,stage=payload.get('stage'),kind_received=payload['kind'])
                    if payload['kind']=='diagnostic_partial':
                        (output/f'{sequence:03d}-{operation}-partial.json').write_text(json.dumps(payload,ensure_ascii=True,indent=2),encoding='utf-8')
                elif payload.get('kind')=='diagnostic_observation':
                    record('diagnostic_observation',sequence=sequence,data=payload['data'])
        record('operation_begin',sequence=sequence,operation=operation)
        body=bridge
        if operation=='logistics':
            # Reuse the production native wrappers and identity/teardown contract.
            marker='// The status path never invokes'
            if bridge.count(marker)!=1:raise RuntimeError('diagnostic_bridge_prefix_changed')
            body=bridge.split(marker)[0]+Path(__file__).with_name('hunt_logistics_readonly.js').read_text(encoding='utf-8')
        elif operation=='observe_replenish':
            marker='let busy=false, finished=false;'
            if bridge.count(marker)!=1:raise RuntimeError('diagnostic_bridge_prefix_changed')
            body=bridge.split(marker)[0]+Path(__file__).with_name('hunt_replenish_observer.js').read_text(encoding='utf-8')
        current_script=session.create_script('const OPTIONS='+json.dumps(options)+';\n'+helper+'\n'+body)
        current_script.on('message',receive)
        safe_to_detach=False
        try:
            current_script.load()
            if not complete.wait(130 if operation=='observe_replenish' else 20):raise RuntimeError('diagnostic_operation_timeout')
            if errors:raise RuntimeError(errors[0])
            (output/f'{sequence:03d}-{operation}.json').write_text(json.dumps(result,ensure_ascii=True,indent=2),encoding='utf-8')
            return result['data']
        finally:
            if not detached.is_set():
                state=drain_native_script(current_script,timeout=5)
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
        if args.logistics_only:
            read('logistics')
            record('checks_complete',operations=sequence,gameplay_commands=0)
            return
        if args.observe_replenish:
            roster=read('roster')
            for raw in roster.get('armies',[]):
                army=hunt_client.normalize_army(raw)
                if sequence>=6:break
                if army['kind']=='regular' and army['formed'] and army['phase'] in hunt_client.READY_PHASES:
                    read('ready',army_id=int(army['id']))
            read('observe_replenish')
            record('checks_complete',operations=sequence,observer_generated_commands=0)
            return
        roster=read('roster')
        ready=[]
        for raw in roster.get('armies',[]):
            army=hunt_client.normalize_army(raw)
            if army['kind']=='regular' and army['formed'] and army['phase'] in hunt_client.READY_PHASES:
                if len(ready)>=5:break
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
