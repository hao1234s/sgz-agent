"""Explicitly enabled, single-session hunt reads and durable passive events.

No gameplay writes are implemented. A failed or timed-out runtime is terminal;
creating another generation requires an explicit new instance and fresh checks.
"""
import json
import importlib.util
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue
import sqlite3
import threading
import time
from uuid import uuid4

from . import client_api
from .client_api import _bridge_lock, readiness
from .native_session import close_native_session, retained_quarantine


class HuntRequestRejected(RuntimeError):
    """A guarded game-state read was refused without a native runtime fault."""


REQUEST_REJECTIONS = frozenset({'army_not_ready_for_details'})


def _controlled_readiness(config):
    """Base read checks for an explicitly authorized diagnostic generation.

    The historical incident stays on disk. In-process retained sessions always
    block instrumentation, including this controlled path.
    """
    if retained_quarantine(config):
        return 'native_quiescence_unconfirmed'
    if not all(config.identity.get(k) for k in
               ('world', 'role_id', 'region', 'api_server_id', 'api_season')):
        return 'api_identity_binding_required'
    if importlib.util.find_spec('frida') is None:
        return 'api_dependency_missing:install_extra_api'
    try:
        stat = config.executable.stat()
        actual = client_api._fingerprint(str(config.executable), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return 'api_client_not_found'
    if actual != client_api.SUPPORTED_SHA256:
        return 'api_client_version_not_verified'
    return None


class HuntEventStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS hunt_runtime_events ('
                       'generation TEXT NOT NULL, sequence INTEGER NOT NULL, '
                       'captured_at REAL NOT NULL, payload TEXT NOT NULL, '
                       'PRIMARY KEY(generation,sequence))')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def append(self, generation, sequence, event):
        if not isinstance(generation, str) or not generation or type(sequence) is not int or sequence < 1:
            raise ValueError('hunt_event_identity_invalid')
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT payload FROM hunt_runtime_events WHERE generation=? AND sequence=?',
                                  (generation, sequence)).fetchone()
            if previous:
                if previous[0] != payload:
                    raise ValueError('hunt_event_sequence_conflict')
                return False
            last = db.execute('SELECT COALESCE(MAX(sequence),0) FROM hunt_runtime_events WHERE generation=?',
                              (generation,)).fetchone()[0]
            if sequence != last + 1:
                raise ValueError('hunt_event_sequence_gap')
            db.execute('INSERT INTO hunt_runtime_events VALUES (?,?,?,?)',
                       (generation, sequence, time.time(), payload))
        return True

    def events(self, *, generation=None, after_sequence=0, limit=1000):
        if type(after_sequence) is not int or after_sequence < 0 or type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('hunt_event_query_invalid')
        where, args = 'sequence>?', [after_sequence]
        if generation is not None:
            where += ' AND generation=?'
            args.append(generation)
        with self._connect() as db:
            rows = db.execute('SELECT generation,sequence,captured_at,payload FROM hunt_runtime_events WHERE '
                              + where + ' ORDER BY captured_at,generation,sequence LIMIT ?', (*args, limit)).fetchall()
        return [{'generation': r[0], 'sequence': r[1], 'captured_at': r[2], 'event': json.loads(r[3])} for r in rows]


class HuntNativeRuntime:
    OPERATIONS = frozenset({'roster', 'ready', 'bases', 'cache_search'})

    def __init__(self, config, *, enabled=False, stop_event=None, event_store=None,
                 on_event=None, controlled_verification=False, request_timeout=15,
                 startup_timeout=15, heartbeat_timeout=90):
        self.config, self.enabled = config, enabled
        self.stop_event = stop_event or threading.Event()
        self.events = event_store or HuntEventStore(config.data_dir / 'hunt-runtime-events.sqlite3')
        self.on_event = on_event
        self.controlled_verification = controlled_verification is True
        self.request_timeout, self.startup_timeout = request_timeout, startup_timeout
        self.heartbeat_timeout = heartbeat_timeout
        self.generation = uuid4().hex
        self._state_lock, self._call_lock, self._close_lock = threading.RLock(), threading.Lock(), threading.Lock()
        self._phase, self._fault, self._cleanup_fault = 'new', None, None
        self._bridge_owned = False
        self._session = self._script = None
        self._ready, self._closed = threading.Event(), threading.Event()
        self._pending = None
        self._last_heartbeat = self._last_sequence = 0
        self._cleanup_scheduled = False
        self._callback_queue = Queue(maxsize=256)

    def state(self):
        with self._state_lock:
            return {'generation': self.generation, 'phase': self._phase, 'fault': self._fault,
                    'cleanup_fault': self._cleanup_fault, 'last_sequence': self._last_sequence,
                    'last_heartbeat': self._last_heartbeat,
                    'bridge_retained': self._phase == 'quarantined' and self._bridge_owned}

    def _quarantine(self, reason):
        marker = Path(self.config.data_dir) / 'hunt-client-quarantine.json'
        try:
            try: previous = json.loads(marker.read_text(encoding='utf-8'))
            except (OSError, ValueError): previous = {}
            if not isinstance(previous, dict): previous = {}
            marker.parent.mkdir(parents=True, exist_ok=True)
            temporary = marker.with_name(marker.name + '.' + self.generation + '.pending')
            temporary.write_text(json.dumps({**previous, 'active': True,
                'incident_id': previous.get('incident_id', 'hunt_runtime_failed'),
                'status': 'hunt_runtime_failed', 'reason': str(reason),
                'runtime_generation': self.generation, 'recorded_at': time.time(),
                'automatic_recovery_allowed': False}, ensure_ascii=False), encoding='utf-8')
            temporary.replace(marker)
        except OSError:
            pass  # The instance still fails closed; cleanup failures retain the bridge.

    def _trip(self, reason, *, quarantine=True):
        with self._state_lock:
            if self._phase in {'closed', 'quarantined'}:
                return
            if self._fault is None:
                self._fault = str(reason)
                if quarantine:
                    self._quarantine(reason)
            if self._pending:
                self._pending['error'] = self._fault
                self._pending['done'].set()
            self._ready.set()
            launch = self._phase == 'running' and not self._cleanup_scheduled
            if launch:
                self._cleanup_scheduled = True
        if launch:
            threading.Thread(target=self._close_after_failure, name='hunt-runtime-failure-close', daemon=True).start()

    def _close_after_failure(self):
        try: self.close()
        except Exception: pass  # The fault and retained cleanup state are observable.

    def _receive(self, message, data):
        if message.get('type') == 'error':
            self._trip('hunt_runtime_script_error:' + str(message.get('description', 'unknown')))
            return
        if message.get('type') != 'send':
            return
        payload = message.get('payload', {})
        try:
            with self._state_lock:
                if self._phase in {'closing', 'closed', 'quarantined'}:
                    return
                if payload.get('generation') != self.generation:
                    raise RuntimeError('hunt_runtime_generation_mismatch')
                kind = payload.get('kind')
                if kind in {'runtime_ready', 'runtime_heartbeat'}:
                    self._last_heartbeat = time.monotonic()
                    if kind == 'runtime_ready': self._ready.set()
                elif kind == 'runtime_failure':
                    raise RuntimeError(str(payload.get('reason', 'hunt_runtime_native_failure')))
                elif kind == 'request_result':
                    pending = self._pending
                    if not pending or payload.get('id') != pending['id'] or pending['done'].is_set():
                        return  # Never replay, or adopt a late result into another call.
                    if payload.get('operation') != pending['operation']:
                        raise RuntimeError('hunt_runtime_operation_mismatch')
                    pending['data'] = payload['data']
                    pending['done'].set()
                elif kind == 'request_rejected':
                    pending = self._pending
                    if not pending or payload.get('id') != pending['id'] or pending['done'].is_set():
                        return
                    if payload.get('operation') != pending['operation']:
                        raise RuntimeError('hunt_runtime_operation_mismatch')
                    reason = payload.get('reason')
                    if reason not in REQUEST_REJECTIONS or pending['operation'] not in {'ready', 'attack_inputs'}:
                        raise RuntimeError('hunt_runtime_unrecognized_rejection:' + str(reason))
                    pending['rejection'] = reason
                    pending['done'].set()
                elif kind == 'protocol_event':
                    event = {k: payload[k] for k in ('name', 'direction', 'data', 'epoch')}
                    event['identity'] = dict(self.config.identity)
                    sequence = payload.get('sequence')
                    inserted = self.events.append(self.generation, sequence, event)
                    self._last_sequence = max(self._last_sequence, sequence)
                    if inserted and self.on_event:
                        # User callbacks may request a read. Do not invoke them
                        # on Frida's message thread or under its state lock.
                        self._callback_queue.put_nowait({'generation': self.generation, 'sequence': sequence, **event})
                else:
                    raise RuntimeError('hunt_runtime_message_not_allowed')
        except Exception as error:
            self._trip(str(error))

    def _source(self):
        identity = self.config.identity
        options = {'path': str(self.config.executable).replace('\\', '/').lower(),
                   'role_id': str(identity['role_id']), 'region': identity['region'],
                   'server_id': identity['api_server_id'], 'season': identity['api_season'],
                   'generation': self.generation}
        root = Path(__file__).parent
        helpers = (root / 'hunt_client.js').read_text(encoding='utf-8')
        marker = 'let busy=false, finished=false;'
        if helpers.count(marker) != 1:
            raise RuntimeError('hunt_runtime_helper_boundary_changed')
        return ('const OPTIONS = ' + json.dumps(options) + ';\n' +
                (root / 'native_lifecycle.js').read_text(encoding='utf-8') + '\n' + helpers.split(marker)[0] + '\n' +
                (root / 'hunt_observer.js').read_text(encoding='utf-8') + '\n' +
                (root / 'hunt_runtime.js').read_text(encoding='utf-8'))

    def start(self):
        with self._close_lock:
            if self.enabled is not True:
                raise RuntimeError('hunt_runtime_explicit_enable_required')
            with self._state_lock:
                if self._phase == 'running': return self
                if self._phase != 'new': raise RuntimeError('hunt_runtime_cannot_restart')
                if self.stop_event.is_set(): raise RuntimeError('hunt_runtime_stopped')
            if retained_quarantine(self.config):
                raise RuntimeError('native_quiescence_unconfirmed')
            marker = Path(self.config.data_dir) / 'hunt-client-quarantine.json'
            if not self.controlled_verification and marker.exists():
                try: active = json.loads(marker.read_text(encoding='utf-8')).get('active') is not False
                except (OSError, ValueError, AttributeError): active = True
                if active: raise RuntimeError('hunt_runtime_quarantined')
            reason = _controlled_readiness(self.config) if self.controlled_verification else readiness(self.config)
            if reason: raise RuntimeError(reason)
            if not _bridge_lock.acquire(blocking=False): raise RuntimeError('api_bridge_busy')
            self._bridge_owned = True
            with self._state_lock: self._phase = 'starting'
            try:
                import frida
                source = self._source()
                device = frida.get_local_device()
                processes = [p for p in device.enumerate_processes()
                             if p.name.lower() == self.config.executable.name.lower()]
                if len(processes) != 1: raise RuntimeError('hunt_runtime_single_client_required')
                token = frida.Cancellable()
                timer = threading.Timer(self.startup_timeout, token.cancel)
                timer.daemon = True
                timer.start()
                try:
                    with token:
                        self._session = device.attach(processes[0].pid)
                        self._session.on('detached', lambda *args: self._unexpected_detach())
                        self._script = self._session.create_script(source)
                        self._script.on('message', self._receive)
                        self._script.load()
                finally:
                    timer.cancel()
                deadline = time.monotonic() + self.startup_timeout
                while not self._ready.wait(.02):
                    if self.stop_event.is_set(): raise RuntimeError('hunt_runtime_stopped')
                    if time.monotonic() >= deadline: raise TimeoutError('hunt_runtime_startup_timeout')
                if self._fault: raise RuntimeError(self._fault)
                if self.stop_event.is_set(): raise RuntimeError('hunt_runtime_stopped')
                with self._state_lock: self._phase = 'running'
                if self.on_event:
                    threading.Thread(target=self._dispatch_callbacks, name='hunt-runtime-events', daemon=True).start()
                threading.Thread(target=self._watch, name='hunt-runtime-health', daemon=True).start()
                return self
            except Exception as error:
                self._trip(str(error))
                self._close_locked()
                raise

    def _unexpected_detach(self):
        with self._state_lock:
            expected = self._phase in {'closing', 'closed', 'quarantined'}
        if not expected: self._trip('hunt_runtime_client_detached')

    def _watch(self):
        while not self._closed.wait(.02):
            if self.stop_event.is_set():
                self._trip('hunt_runtime_stopped', quarantine=False)
                return
            if time.monotonic() - self._last_heartbeat > self.heartbeat_timeout:
                self._trip('hunt_runtime_heartbeat_timeout')
                return

    def _dispatch_callbacks(self):
        while not self._closed.is_set():
            try: event = self._callback_queue.get(timeout=.05)
            except Empty: continue
            try:
                if not self._closed.is_set(): self.on_event(event)
            except Exception as error:
                self._trip('hunt_runtime_event_callback_failed:' + str(error))
                return

    def _rpc_enqueue(self, job, deadline):
        done, outcome = threading.Event(), {}
        def enqueue():
            try: outcome['ack'] = self._script.exports_sync.enqueue(job)
            except Exception as error: outcome['error'] = error
            finally: done.set()
        threading.Thread(target=enqueue, name='hunt-runtime-enqueue', daemon=True).start()
        while not done.wait(.02):
            if self._fault: raise RuntimeError(self._fault)
            if time.monotonic() >= deadline: raise TimeoutError('hunt_runtime_result_unknown:enqueue_timeout')
        if 'error' in outcome: raise outcome['error']
        if outcome.get('ack') != {'queued': job['id']}:
            raise RuntimeError('hunt_runtime_enqueue_unconfirmed')

    @staticmethod
    def _arguments(operation, arguments):
        if operation not in HuntNativeRuntime.OPERATIONS:
            raise ValueError('hunt_runtime_operation_not_allowed')
        if operation in {'roster', 'bases'}:
            if arguments: raise ValueError('hunt_runtime_arguments_invalid')
        elif operation == 'ready':
            if set(arguments) != {'army_id'} or isinstance(arguments['army_id'], bool):
                raise ValueError('hunt_runtime_arguments_invalid')
            value = arguments['army_id']
            if not str(value).isdigit() or not 0 < int(value) < 2**53:
                raise ValueError('hunt_runtime_army_id_invalid')
            arguments = {'army_id': int(value)}
        else:
            if set(arguments) != {'center', 'radius'}:
                raise ValueError('hunt_runtime_arguments_invalid')
            center, radius = arguments['center'], arguments['radius']
            if (not isinstance(center, (list, tuple)) or len(center) != 2 or
                    any(type(v) is not int or not 1 <= v <= 1620 for v in center) or
                    type(radius) is not int or not 1 <= radius <= 70):
                raise ValueError('hunt_runtime_search_bounds_invalid')
            arguments = {'center': list(center), 'radius': radius}
        return arguments

    def request(self, operation, *, timeout=None, **arguments):
        arguments = self._arguments(operation, arguments)
        duration = self.request_timeout if timeout is None else timeout
        if type(duration) not in {int, float} or not 0 < duration <= 60:
            raise ValueError('hunt_runtime_timeout_invalid')
        with self._call_lock:
            with self._state_lock:
                if self.stop_event.is_set():
                    self._trip('hunt_runtime_stopped', quarantine=False)
                    raise RuntimeError('hunt_runtime_stopped')
                if self._phase != 'running' or self._fault:
                    raise RuntimeError('hunt_runtime_unavailable:' + str(self._fault or self._phase))
                pending = {'id': uuid4().hex, 'operation': operation, 'done': threading.Event()}
                self._pending = pending
            deadline = time.monotonic() + duration
            try:
                self._rpc_enqueue({'id': pending['id'], 'operation': operation, 'args': arguments}, deadline)
                while not pending['done'].wait(.02):
                    if self.stop_event.is_set():
                        self._trip('hunt_runtime_stopped', quarantine=False)
                    if time.monotonic() >= deadline:
                        raise TimeoutError('hunt_runtime_result_unknown:request_timeout')
                if 'error' in pending: raise RuntimeError(pending['error'])
                if 'rejection' in pending: raise HuntRequestRejected(pending['rejection'])
                return pending['data']
            except HuntRequestRejected:
                raise
            except Exception as error:
                self._trip(str(error), quarantine='stopped' not in str(error))
                raise
            finally:
                with self._state_lock:
                    if self._pending is pending: self._pending = None

    def roster_status(self, *, timeout=None):
        return self.request('roster', timeout=timeout)

    def ready_army(self, army_id, *, timeout=None):
        return self.request('ready', army_id=army_id, timeout=timeout)

    def bases(self, *, timeout=None):
        return self.request('bases', timeout=timeout)

    def cache_search(self, center, radius=70, *, timeout=None):
        return self.request('cache_search', center=center, radius=radius, timeout=timeout)

    def _close_locked(self):
        with self._state_lock:
            if self._phase == 'closed': return
            if self._phase == 'quarantined':
                raise RuntimeError(self._cleanup_fault or 'hunt_runtime_cleanup_quarantined')
            self._phase = 'closing'
            if self._pending:
                self._pending['error'] = self._fault or 'hunt_runtime_closed'
                self._pending['done'].set()
        try:
            if self._session is not None:
                close_native_session(self.config, self._session, self._script)
        except Exception as error:
            with self._state_lock:
                self._cleanup_fault = str(error)
                self._phase = 'quarantined'
            self._quarantine((self._fault or 'hunt_runtime_close_failed') + ':cleanup:' + str(error))
            self._closed.set()
            raise
        with self._state_lock:
            self._phase = 'closed'
            if self._bridge_owned:
                self._bridge_owned = False
                _bridge_lock.release()
        self._closed.set()

    def close(self):
        with self._close_lock:
            self._close_locked()

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
