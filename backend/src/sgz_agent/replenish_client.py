"""Only verified own-city ordinary replenishment; no arbitrary game command API."""
from hashlib import sha256
import json
from pathlib import Path
import threading
import time

from .client_api import _bridge_lock, _fingerprint, SUPPORTED_SHA256
from .native_session import close_native_session, retained_quarantine


def incident_digest(config):
    path = config.data_dir / 'hunt-client-quarantine.json'
    return sha256(path.read_bytes() if path.exists() else b'').hexdigest()


def code_digest():
    root = Path(__file__).parent
    return sha256(b''.join((root/name).read_bytes() for name in
        ('replenish.py','replenish_client.py','replenish_client.js','hunt_client.js','native_lifecycle.js','native_session.py'))).hexdigest()


SCOPES = {'execute':'own_city_ordinary_replenishment_902',
          'snapshot':'own_city_ordinary_replenishment_snapshot'}


def validation_path(config, operation='execute'):
    if operation not in SCOPES: raise ValueError('replenish_operation_not_allowed')
    return config.data_dir / ('replenish-read-validation.json' if operation=='snapshot'
                              else 'replenish-api-validation.json')


def save_validation(config, *, evidence, operation='execute'):
    path = validation_path(config,operation)
    record = {'active':True, 'scope':SCOPES[operation],
              'identity':config.identity, 'client_sha256':SUPPORTED_SHA256,
              'incident_sha256':incident_digest(config), 'code_sha256':code_digest(),
              'evidence':evidence, 'validated_at':time.time()}
    temporary=path.with_suffix('.pending')
    temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    temporary.replace(path)


def revoke_validation(config, reason, *, include_read=False):
    record=json.dumps({'active':False,'reason':reason,'revoked_at':time.time()})
    for operation in ('execute','snapshot') if include_read else ('execute',):
        validation_path(config,operation).write_text(record,encoding='utf-8')


def replenish_readiness(config, *, controlled_verification=False, operation='execute'):
    if operation not in SCOPES: raise ValueError('replenish_operation_not_allowed')
    if operation == 'execute':
        from .hunt_attack import pending_attack
        if pending_attack(config): return 'controlled_attack_pending'
    if retained_quarantine(config): return 'native_quiescence_unconfirmed'
    if not all(config.identity.get(k) for k in ('world','role_id','region','api_server_id','api_season')):
        return 'replenish_identity_missing'
    try:
        stat=config.executable.stat()
        if _fingerprint(str(config.executable),stat.st_size,stat.st_mtime_ns)!=SUPPORTED_SHA256:
            return 'replenish_client_version_unverified'
    except OSError: return 'replenish_client_missing'
    if controlled_verification: return None
    reason=_scope_readiness(config,operation)
    if operation=='snapshot' and reason and _scope_readiness(config,'execute') is None:
        return None
    return reason


def _scope_readiness(config,operation):
    required='replenish_read_validation_required' if operation=='snapshot' else 'replenish_validation_required'
    try: record=json.loads(validation_path(config,operation).read_text(encoding='utf-8'))
    except (OSError,ValueError): return required
    if not isinstance(record,dict) or record.get('active') is not True: return required
    if record.get('scope')!=SCOPES[operation] or record.get('client_sha256')!=SUPPORTED_SHA256:
        return 'replenish_scope_invalid'
    if record.get('identity')!=config.identity: return 'replenish_identity_changed'
    if record.get('incident_sha256')!=incident_digest(config): return 'replenish_incident_changed'
    if record.get('code_sha256')!=code_digest(): return 'replenish_implementation_changed'
    return None


class ReplenishClient:
    def __init__(self,config,*,stop_event=None,controlled_verification=False):
        self.config=config
        self.stop_event=stop_event or threading.Event()
        self.controlled_verification=controlled_verification

    def snapshot(self,army_ids=None):
        return self._run('snapshot',army_ids=army_ids)

    def ensure_execution_ready(self):
        reason=replenish_readiness(self.config,controlled_verification=self.controlled_verification)
        if reason: raise RuntimeError(reason)

    def execute(self,plan):
        return self._run('execute',plan=plan)

    def _run(self,operation,**params):
        if operation not in {'snapshot','execute'}: raise ValueError('replenish_operation_not_allowed')
        reason=replenish_readiness(self.config,controlled_verification=self.controlled_verification,
                                  operation=operation)
        if reason: raise RuntimeError(reason)
        if self.stop_event.is_set(): raise RuntimeError('stopped')
        if not _bridge_lock.acquire(blocking=False): raise RuntimeError('api_bridge_busy')
        session=script=cancel=watcher=None
        done=threading.Event();watcher_done=threading.Event();result={};errors=[]
        deadline=time.monotonic()+30
        try:
            import frida
            cancel=frida.Cancellable();cancel.__enter__()
            def watch():
                while not watcher_done.wait(.05):
                    if self.stop_event.is_set() or time.monotonic()>=deadline:
                        cancel.cancel();return
            watcher=threading.Thread(target=watch,daemon=True,name='replenish-cancel');watcher.start()
            device=frida.get_local_device()
            processes=[p for p in device.enumerate_processes() if p.name.lower()==self.config.executable.name.lower()]
            if len(processes)!=1:raise RuntimeError('replenish_single_client_required')
            identity=self.config.identity
            options={'path':str(self.config.executable).replace('\\','/').lower(),
                     'role_id':str(identity['role_id']),'region':identity['region'],
                     'server_id':identity['api_server_id'],'season':identity['api_season'],
                     'operation':operation,**params}
            root=Path(__file__).parent
            helpers=(root/'hunt_client.js').read_text(encoding='utf-8')
            marker='let busy=false, finished=false;'
            if helpers.count(marker)!=1:raise RuntimeError('replenish_helper_boundary_changed')
            source=('const OPTIONS='+json.dumps(options)+';\n'+
                    (root/'native_lifecycle.js').read_text(encoding='utf-8')+'\n'+
                    helpers.split(marker)[0]+'\n'+(root/'replenish_client.js').read_text(encoding='utf-8'))
            session=device.attach(processes[0].pid)
            session.on('detached',lambda *args:(errors.append('replenish_client_detached'),done.set()) if not done.is_set() else None)
            script=session.create_script(source)
            def receive(message,data):
                if message['type']=='error':errors.append(message.get('description','script_error'));done.set()
                elif message['type']=='send':
                    payload=message['payload']
                    if payload.get('kind')=='complete':
                        result.update(payload['data'])
                        result['native_lifecycle']=payload.get('lifecycle')
                        done.set()
                    elif payload.get('kind')=='failure':errors.append(payload['reason']);done.set()
            script.on('message',receive);script.load()
            while not done.wait(.05):
                if self.stop_event.is_set():raise RuntimeError('stopped')
                if time.monotonic()>=deadline:raise TimeoutError('replenish_timeout')
            if errors:raise RuntimeError(errors[0])
            if self.stop_event.is_set():raise RuntimeError('stopped')
            if operation=='execute' and result.get('status')=='unknown':
                revoke_validation(self.config,result.get('reason','replenish_result_unknown'))
            return result
        except Exception as error:
            revoke_validation(self.config,str(error),include_read=True);raise
        finally:
            watcher_done.set()
            if watcher:watcher.join(1)
            if cancel:cancel.__exit__(None,None,None)
            try:
                close_native_session(self.config,session,script)
                if result and not errors:
                    result['cleanup_verified']=True
                    if operation=='snapshot':
                        # The native bridge verifies these bound fields before and after
                        # its observation. Publish identity only after successful cleanup.
                        result['identity']=dict(self.config.identity)
            except Exception as error:
                revoke_validation(self.config,'cleanup:'+str(error),include_read=True);raise
            finally:_bridge_lock.release()
