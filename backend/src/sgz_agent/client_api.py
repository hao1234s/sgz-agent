"""Bounded read-only bridge to the logged-in client's existing Lua APIs.

No socket authentication, arbitrary Lua evaluation, or gameplay commands.
Resources describe map configuration; NPC responses cover personal spawns.
"""
from functools import lru_cache
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import threading
import time

from .map_query import bounds, RESOURCES
from .bandits import classify_npc
from .native_session import close_native_session, retained_quarantine


SUPPORTED_SHA256 = '9de3749a3f9190bc4b6da53473bb30853d3a92cff8fccd5b2f5abebcb0451d88'
_bridge_lock = threading.Lock()


def validate_region(x0, y0, x1, y1):
    count = bounds(x0, y0, x1, y1)
    if min(x0, y0) < 1 or max(x1, y1) > 1620:
        raise ValueError('api_coordinate_out_of_range:1..1620')
    if count > 2500:
        raise ValueError('api_region_too_large:2500')
    return count


@lru_cache(maxsize=4)
def _fingerprint(path, size, modified):
    with open(path, 'rb') as stream:
        digest = sha256()
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def readiness(config):
    if retained_quarantine(config):
        return 'api_runtime_quarantined:native_quiescence_unconfirmed'
    directory = getattr(config, 'data_dir', None)
    if directory is not None:
        marker = Path(directory) / 'hunt-client-quarantine.json'
        try:
            incident = json.loads(marker.read_text(encoding='utf-8'))
        except FileNotFoundError:
            incident = None
        except (OSError, ValueError):
            return 'api_runtime_quarantined:unreadable_incident_record'
        if incident is not None:
            if not isinstance(incident,dict):
                return 'api_runtime_quarantined:invalid_incident_record'
            if incident.get('active') is not False:
                return 'api_runtime_quarantined:' + str(incident.get('incident_id','unknown'))
    identity = config.identity
    if not all(identity.get(k) for k in ('role_id', 'region', 'api_server_id', 'api_season', 'world')):
        return 'api_identity_binding_required'
    if importlib.util.find_spec('frida') is None:
        return 'api_dependency_missing:install_extra_api'
    try:
        stat = config.executable.stat()
        actual = _fingerprint(str(config.executable), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return 'api_client_not_found'
    if actual != SUPPORTED_SHA256:
        return 'api_client_version_not_verified'
    return None


def normalize_scan(raw):
    bandits = {}
    for item in raw['bandits']:
        key = (item['x'], item['y'])
        if key in bandits and bandits[key]['id'] != item['id']:
            raise RuntimeError('multiple_bandits_at_coordinate')
        bandits[key] = {**item, **classify_npc(item), 'scope': 'personal',
                        'source': 'server_query'}
    rows = []
    for item in raw['resources']:
        name = item.get('name')
        category = next((r for r in RESOURCES if r in (name or '')), None)
        # A missing map config stays unknown, including mountains/invalid cells.
        if category is None and name:
            category = 'other'
        fields = {'name': name, 'category': category, 'level': item.get('level'),
                  'resource_config_id': item.get('id'), 'source': 'client_api',
                  'resource_source': 'client_map_config', 'occupancy': 'unknown',
                  'bandit_scope': 'personal', 'bandit_checked': False,
                  'bandit': bandits.get((item['x'], item['y']))}
        rows.append({'x': item['x'], 'y': item['y'], 'fields': fields})
    return rows


def scan(config, region, stop_event, *, timeout=45):
    validate_region(**region)
    if stop_event.is_set():
        raise RuntimeError('stopped')
    reason = readiness(config)
    if reason:
        raise RuntimeError(reason)
    if not _bridge_lock.acquire(blocking=False):
        raise RuntimeError('api_bridge_busy')
    session = script = None
    completed = threading.Event()
    result = {'resources': []}
    errors = []
    started = time.monotonic()
    control_done = threading.Event()
    cancellable = None
    watcher = None

    def check_cancel():
        if stop_event.is_set():
            raise RuntimeError('stopped')
        if time.monotonic() - started >= timeout:
            raise RuntimeError('api_scan_timeout:no_complete_server_response')

    try:
        import frida
        cancellable = frida.Cancellable()
        cancellable.__enter__()

        def watch():
            while not control_done.wait(.02):
                if stop_event.is_set() or time.monotonic() - started >= timeout:
                    cancellable.cancel()
                    return

        watcher = threading.Thread(target=watch, daemon=True, name='sgz-api-cancel')
        watcher.start()
        check_cancel()
        device = frida.get_local_device()
        check_cancel()
        processes = [p for p in device.enumerate_processes() if p.name.lower() == config.executable.name.lower()]
        check_cancel()
        if len(processes) != 1:
            raise RuntimeError(f'api_client_process_count:{len(processes)}')
        identity = config.identity
        options = {'path': str(config.executable).replace('\\', '/').lower(),
                   'role_id': str(identity['role_id']), 'region': identity['region'],
                   'server_id': identity['api_server_id'], 'season': identity['api_season'],
                   **region}
        source = Path(__file__).with_name('client_api.js').read_text(encoding='utf-8')
        source = ('const OPTIONS = ' + json.dumps(options, ensure_ascii=True) + ';\n'
                  + Path(__file__).with_name('native_lifecycle.js').read_text(encoding='utf-8') + '\n' + source)
        check_cancel()
        session = device.attach(processes[0].pid)
        check_cancel()

        def detached(*args):
            if not completed.is_set():
                errors.append('api_client_detached')
                completed.set()

        session.on('detached', detached)
        script = session.create_script(source)
        check_cancel()

        def message(msg, data):
            if msg['type'] == 'error':
                errors.append('api_script_error:' + msg.get('description', 'unknown'))
                completed.set()
            elif msg['type'] == 'send':
                payload = msg['payload']
                if payload['kind'] == 'chunk':
                    result['resources'].extend(payload['items'])
                elif payload['kind'] == 'complete':
                    result.update(payload)
                    completed.set()
                elif payload['kind'] == 'failure':
                    errors.append(payload['reason'])
                    completed.set()

        script.on('message', message)
        check_cancel()
        script.load()
        check_cancel()
        while not completed.wait(.05):
            check_cancel()
        check_cancel()
        if errors:
            raise RuntimeError(errors[0])
        if stop_event.is_set():
            raise RuntimeError('stopped')
        expected = {(x, y) for x in range(region['x0'], region['x1'] + 1)
                    for y in range(region['y0'], region['y1'] + 1)}
        if len(result['resources']) != len(expected) or {(r['x'], r['y']) for r in result['resources']} != expected:
            raise RuntimeError('api_incomplete_resource_response')
        result['elapsed_seconds'] = round(time.monotonic() - started, 3)
        result['source'] = 'client_api'
        result['coverage'] = {'resource_tiles': len(expected), 'bandits': 'personal_server_list',
                              'world_bandits_complete': False, 'occupancy': 'unknown'}
        return result
    except Exception:
        check_cancel()
        raise
    finally:
        control_done.set()
        if watcher is not None:
            watcher.join(1)
        if cancellable is not None:
            cancellable.__exit__(None, None, None)

        try:
            close_native_session(config,session,script)
        finally:
            _bridge_lock.release()
