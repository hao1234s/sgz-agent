import json
import threading
import time
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest


def runtime_types():
    from sgz_agent.hunt_runtime import HuntEventStore, HuntNativeRuntime
    return HuntEventStore, HuntNativeRuntime


def test_event_journal_survives_reopen_and_rejects_changed_or_missing_sequence(tmp_path):
    Store, _ = runtime_types()
    path = tmp_path / 'events.sqlite3'
    store = Store(path)
    event = {'name': 'sc_sync_army_status', 'data': {'army_info': {'army_id': 7}}}
    assert store.append('generation-a', 1, event) is True
    assert Store(path).events(generation='generation-a')[0]['event'] == event
    assert store.append('generation-a', 1, event) is False
    with pytest.raises(ValueError, match='sequence_conflict'):
        store.append('generation-a', 1, {'different': True})
    with pytest.raises(ValueError, match='sequence_gap'):
        store.append('generation-a', 3, event)
    assert store.events(generation='generation-a', after_sequence=1) == []


class FakeScript:
    def __init__(self, source):
        options = json.loads(source.split('\n', 1)[0].removeprefix('const OPTIONS = ').removesuffix(';'))
        self.generation = options['generation']
        self.message_handler = None
        self.enqueued = []
        self.loaded = False
        self.unloaded = False
        self.auto_complete = True
        self.inflight = self.maximum_inflight = 0
        self.guard = threading.Lock()
        self.exports_sync = SimpleNamespace(enqueue=self.enqueue, close=self.close)

    def on(self, name, handler):
        assert name == 'message'
        self.message_handler = handler

    def emit(self, payload):
        self.message_handler({'type': 'send', 'payload': {'generation': self.generation, **payload}}, None)

    def load(self):
        self.loaded = True
        self.emit({'kind': 'runtime_ready'})

    def enqueue(self, job):
        self.enqueued.append(job)
        with self.guard:
            self.inflight += 1
            self.maximum_inflight = max(self.maximum_inflight, self.inflight)
        if self.auto_complete:
            def complete():
                time.sleep(.01)
                with self.guard:
                    self.inflight -= 1
                self.emit({'kind': 'request_result', 'id': job['id'],
                           'operation': job['operation'], 'data': {'operation': job['operation'], 'args': job['args']}})
            threading.Thread(target=complete, daemon=True).start()
        return {'queued': job['id']}

    def close(self):
        return {'closed': True, 'active_native': 0, 'callbacks': 0, 'hooks': 0}

    def unload(self):
        self.unloaded = True


class FakeSession:
    def __init__(self):
        self.is_detached = False
        self.script = None

    def on(self, name, handler):
        self.detached_handler = handler

    def create_script(self, source):
        self.script = FakeScript(source)
        return self.script

    def detach(self):
        self.is_detached = True


@pytest.fixture
def native_env(tmp_path, monkeypatch):
    _, Runtime = runtime_types()
    import sgz_agent.hunt_runtime as module
    from sgz_agent.client_api import _bridge_lock
    session = FakeSession()
    class Cancellable:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def cancel(self): pass
    device = SimpleNamespace(enumerate_processes=lambda: [SimpleNamespace(name='S3Client.exe', pid=42)],
                             attach=lambda pid: session)
    monkeypatch.setitem(__import__('sys').modules, 'frida', SimpleNamespace(
        get_local_device=lambda: device, Cancellable=Cancellable))
    monkeypatch.setattr(module, 'readiness', lambda config: None)
    config = SimpleNamespace(data_dir=tmp_path, executable=tmp_path / 'S3Client.exe', identity={
        'world': 'test-world', 'role_id': '7', 'region': 'test-region',
        'api_server_id': 3, 'api_season': 4})
    runtimes = []
    def create(**kwargs):
        runtime = Runtime(config, enabled=True, request_timeout=.3, startup_timeout=.3, **kwargs)
        runtimes.append(runtime)
        return runtime
    yield create, session, config
    for runtime in runtimes:
        try: runtime.close()
        except RuntimeError: pass
    # The cleanup-failure test deliberately retains the bridge to block reattach.
    for runtime in runtimes:
        if runtime.state().get('bridge_retained'):
            _bridge_lock.release()


def test_opt_in_and_whitelist_reject_without_native_attach(native_env):
    create, session, config = native_env
    _, Runtime = runtime_types()
    with pytest.raises(RuntimeError, match='explicit_enable'):
        Runtime(config).start()
    assert session.script is None
    runtime = create().start()
    with pytest.raises(ValueError, match='operation_not_allowed'):
        runtime.request('network.call')
    with pytest.raises(ValueError, match='arguments_invalid'):
        runtime.request('ready', army_id=123, include_battle=True)
    with pytest.raises(ValueError, match='arguments_invalid'):
        runtime.request('ready', army_id=True)
    assert session.script.enqueued == []


def test_closing_unstarted_runtime_needs_no_native_api(native_env, monkeypatch):
    create, _, _ = native_env
    import sgz_agent.hunt_runtime as module
    def forbidden(*args, **kwargs):
        raise AssertionError('native cleanup must not run before attach')
    monkeypatch.setattr(module, 'close_native_session', forbidden)
    runtime = create()
    runtime.close()
    assert runtime.state()['phase'] == 'closed'


def test_stop_before_request_does_not_enqueue(native_env):
    create, session, _ = native_env
    stop = threading.Event()
    runtime = create(stop_event=stop).start()
    stop.set()
    with pytest.raises(RuntimeError, match='stopped'):
        runtime.roster_status()
    assert session.script.enqueued == []


def test_expected_not_ready_rejection_preserves_runtime_and_incident(native_env):
    create, session, config = native_env
    runtime = create().start()
    marker = config.data_dir / 'hunt-client-quarantine.json'
    marker.write_text('{"active":true,"reason":"prior incident"}', encoding='utf-8')
    session.script.auto_complete = False
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.ready_army, '7')
        deadline = time.monotonic() + 1
        while not session.script.enqueued and time.monotonic() < deadline:
            time.sleep(.001)
        job = session.script.enqueued[-1]
        session.script.emit({'kind': 'request_rejected', 'id': job['id'],
                             'operation': 'ready', 'reason': 'army_not_ready_for_details'})
        with pytest.raises(RuntimeError, match='army_not_ready_for_details'):
            future.result(timeout=1)
    assert runtime.state()['phase'] == 'running'
    assert runtime.state()['fault'] is None
    assert marker.read_text(encoding='utf-8') == '{"active":true,"reason":"prior incident"}'
    session.script.auto_complete = True
    assert runtime.roster_status()['operation'] == 'roster'


def test_unrecognized_request_rejection_still_quarantines(native_env):
    create, session, config = native_env
    runtime = create().start()
    session.script.auto_complete = False
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.ready_army, '7')
        deadline = time.monotonic() + 1
        while not session.script.enqueued and time.monotonic() < deadline:
            time.sleep(.001)
        job = session.script.enqueued[-1]
        session.script.emit({'kind': 'request_rejected', 'id': job['id'],
                             'operation': 'ready', 'reason': 'identity_mismatch'})
        with pytest.raises(RuntimeError):
            future.result(timeout=1)
    assert runtime.state()['fault'] is not None
    assert (config.data_dir / 'hunt-client-quarantine.json').exists()


def test_controlled_read_uses_real_marker_but_preserves_it_and_checks_hash_dependency(native_env, monkeypatch):
    create, session, config = native_env
    from sgz_agent import client_api
    from sgz_agent import hunt_runtime as module
    marker = config.data_dir / 'hunt-client-quarantine.json'
    original = '{"active":true,"incident_id":"historical-incident","reason":"original evidence"}'
    marker.write_text(original, encoding='utf-8')
    config.executable.write_bytes(b'verified-version-fixture')
    monkeypatch.setattr(module, 'readiness', client_api.readiness)
    monkeypatch.setattr(client_api, '_fingerprint', lambda *args: client_api.SUPPORTED_SHA256)
    monkeypatch.setattr(client_api.importlib.util, 'find_spec', lambda name: SimpleNamespace(name=name))
    controlled = create(controlled_verification=True).start()
    assert session.script.loaded
    controlled.close()
    assert marker.read_text(encoding='utf-8') == original
    with pytest.raises(RuntimeError, match='quarantined'):
        create().start()
    monkeypatch.setattr(client_api, '_fingerprint', lambda *args: 'wrong-version')
    with pytest.raises(RuntimeError, match='version'):
        create(controlled_verification=True).start()
    monkeypatch.setattr(client_api, '_fingerprint', lambda *args: client_api.SUPPORTED_SHA256)
    monkeypatch.setattr(client_api.importlib.util, 'find_spec', lambda name: None)
    with pytest.raises(RuntimeError, match='dependency'):
        create(controlled_verification=True).start()
    monkeypatch.setattr(client_api.importlib.util, 'find_spec', lambda name: SimpleNamespace(name=name))
    previous_world = config.identity.pop('world')
    with pytest.raises(RuntimeError, match='identity_binding'):
        create(controlled_verification=True).start()
    config.identity['world'] = previous_world
    assert marker.read_text(encoding='utf-8') == original


def test_controlled_read_never_bypasses_retained_native_quarantine(native_env, monkeypatch):
    create, session, config = native_env
    from sgz_agent import hunt_runtime as module
    marker = config.data_dir / 'hunt-client-quarantine.json'
    original = '{"active":true,"incident_id":"historical-incident"}'
    marker.write_text(original, encoding='utf-8')
    monkeypatch.setattr(module, 'retained_quarantine', lambda config: {'active': True})
    with pytest.raises(RuntimeError, match='native_quiescence_unconfirmed'):
        create(controlled_verification=True).start()
    assert session.script is None
    assert marker.read_text(encoding='utf-8') == original


def test_read_calls_are_serial_and_bridge_is_owned_until_close(native_env):
    create, session, _ = native_env
    from sgz_agent.client_api import _bridge_lock
    runtime = create().start()
    assert _bridge_lock.acquire(blocking=False) is False
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: runtime.roster_status(), range(8)))
    assert len(results) == 8
    assert session.script.maximum_inflight == 1
    assert runtime.ready_army('123')['args'] == {'army_id': 123}
    assert runtime.cache_search([10, 20], 70)['operation'] == 'cache_search'
    runtime.close()
    assert session.script.unloaded and session.is_detached
    assert _bridge_lock.acquire(blocking=False)
    _bridge_lock.release()


def test_passive_events_are_durable_before_user_callback_and_deduplicated(native_env):
    create, session, _ = native_env
    seen = []
    runtime = create(on_event=lambda event: seen.append((event, runtime.events.events()))).start()
    packet = {'kind': 'protocol_event', 'sequence': 1, 'name': 'sc_army_unit_update',
              'direction': 'response', 'data': {'entity': {'eid': 99}}, 'epoch': 123}
    session.script.emit(packet)
    session.script.emit(packet)
    deadline = time.monotonic() + 1
    while not seen and time.monotonic() < deadline:
        time.sleep(.005)
    assert len(seen) == 1
    assert seen[0][1][0]['event']['data'] == packet['data']
    assert runtime.state()['last_sequence'] == 1


def test_event_callback_can_request_read_without_blocking_native_message_delivery(native_env):
    create, session, _ = native_env
    seen = []
    runtime = create(on_event=lambda event: seen.append(runtime.roster_status())).start()
    session.script.emit({'kind': 'protocol_event', 'sequence': 1, 'name': 'sc_army_unit_update',
                         'direction': 'response', 'data': {'entity': {'eid': 99}}, 'epoch': 123})
    deadline = time.monotonic() + 1
    while not seen and time.monotonic() < deadline:
        time.sleep(.005)
    assert seen and seen[0]['operation'] == 'roster'
    assert runtime.state()['fault'] is None


def test_stuck_enqueue_rpc_is_bounded_and_never_replayed(native_env):
    create, session, _ = native_env
    runtime = create().start()
    release = threading.Event()
    calls = []
    def stuck(job):
        calls.append(job)
        release.wait(2)
        return {'queued': job['id']}
    session.script.exports_sync.enqueue = stuck
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match='result_unknown'):
            runtime.roster_status(timeout=.03)
        assert time.monotonic() - started < .5
        assert len(calls) == 1
    finally:
        release.set()


def test_health_timeout_closes_silent_runtime(native_env):
    create, session, _ = native_env
    runtime = create(heartbeat_timeout=.04).start()
    deadline = time.monotonic() + 1
    while runtime.state()['phase'] not in {'closed', 'quarantined'} and time.monotonic() < deadline:
        time.sleep(.005)
    assert runtime.state()['fault'] == 'hunt_runtime_heartbeat_timeout'
    assert session.is_detached


def test_timeout_closes_without_replay_and_late_result_cannot_restore_runtime(native_env):
    create, session, _ = native_env
    runtime = create().start()
    session.script.auto_complete = False
    with pytest.raises(TimeoutError, match='result_unknown'):
        runtime.roster_status(timeout=.03)
    assert len(session.script.enqueued) == 1
    job = session.script.enqueued[0]
    session.script.emit({'kind': 'request_result', 'id': job['id'], 'operation': 'roster', 'data': {'late': True}})
    with pytest.raises(RuntimeError, match='runtime_unavailable'):
        runtime.roster_status()
    assert runtime.state()['fault']


def test_stop_wakes_waiting_call_and_safely_closes(native_env):
    create, session, _ = native_env
    stop = threading.Event()
    runtime = create(stop_event=stop).start()
    session.script.auto_complete = False
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runtime.roster_status, timeout=1)
        deadline = time.monotonic() + 1
        while not session.script.enqueued and time.monotonic() < deadline:
            time.sleep(.005)
        stop.set()
        with pytest.raises(RuntimeError, match='stopped'):
            future.result(timeout=1)
    runtime.close()
    assert session.is_detached


def test_native_error_poisoning_is_persisted_and_cleanup_failure_retains_bridge(native_env, monkeypatch):
    create, session, config = native_env
    import sgz_agent.hunt_runtime as module
    runtime = create().start()
    def failed_close(*args, **kwargs):
        raise RuntimeError('native_quiescence_unconfirmed:test')
    monkeypatch.setattr(module, 'close_native_session', failed_close)
    session.script.message_handler({'type': 'error', 'description': 'access violation'}, None)
    deadline = time.monotonic() + 1
    while runtime.state()['phase'] != 'quarantined' and time.monotonic() < deadline:
        time.sleep(.005)
    assert runtime.state()['phase'] == 'quarantined'
    assert runtime.state()['bridge_retained'] is True
    marker = json.loads((config.data_dir / 'hunt-client-quarantine.json').read_text(encoding='utf-8'))
    assert marker['active'] is True and 'access violation' in marker['reason']


def test_javascript_queue_runs_only_on_native_boundary_and_close_cancels_queued_read():
    source = Path('src/sgz_agent/hunt_runtime.js').read_text(encoding='utf-8')
    harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
let hook,messages=[],calls=[],immediate=[];
const context={OPTIONS:{generation:'test-generation'},rpc:{exports:{}},
  client:{getExportByName:()=>1},lifecycle:{attach:(a,h)=>{hook=h},
    close:()=>Promise.resolve({closed:true}),finish:m=>messages.push(m),state:()=>({closed:false})},
  send:m=>messages.push(m),setImmediate:f=>immediate.push(f),setInterval:()=>1,clearInterval:()=>{},
  top:()=>0,settop:()=>{},checkstack:()=>true,identity:()=>{},
  roster:()=>{calls.push('roster');return {complete:true,armies:[]}},
  readyArmy:()=>({}),bases:()=>({}),cachedGiants:()=>({complete:false,targets:[]}),
  createHuntObserver:()=>({install:()=>{},close:()=>{},state:()=>({})})};
vm.createContext(context);vm.runInContext(SOURCE,context);
assert.throws(()=>context.rpc.exports.enqueue({id:'x',operation:'network.call',args:{}}),/operation_not_allowed/);
assert.equal(calls.length,0);
hook.onEnter([1]);hook.onLeave();while(immediate.length)immediate.shift()();
assert.equal(messages[0].kind,'runtime_ready');
context.rpc.exports.enqueue({id:'r1',operation:'roster',args:{}});
assert.equal(calls.length,0);
assert.throws(()=>context.rpc.exports.enqueue({id:'r2',operation:'roster',args:{}}),/queue_busy/);
hook.onEnter([1]);assert.equal(calls.length,1);
assert.equal(messages.filter(m=>m.kind==='request_result').length,0);
hook.onLeave();while(immediate.length)immediate.shift()();
assert.equal(messages.filter(m=>m.kind==='request_result').length,1);
context.rpc.exports.enqueue({id:'r3',operation:'roster',args:{}});
context.rpc.exports.close();hook.onEnter([1]);hook.onLeave();
assert.equal(calls.length,1);
assert.throws(()=>context.rpc.exports.enqueue({id:'r4',operation:'roster',args:{}}),/closing/);
'''
    result = subprocess.run(['node', '-e', 'const SOURCE=' + json.dumps(source) + ';\n' + harness],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_javascript_not_ready_is_request_rejection_and_next_status_read_works():
    source = Path('src/sgz_agent/hunt_runtime.js').read_text(encoding='utf-8')
    harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
let hook,messages=[],immediate=[],closed=false;
const context={OPTIONS:{generation:'g'},rpc:{exports:{}},
 client:{getExportByName:()=>1},lifecycle:{attach:(a,h)=>{hook=h},
 close:()=>({closed:true}),finish:m=>{closed=true;messages.push(m)},state:()=>({closed})},
 send:m=>messages.push(m),setImmediate:f=>immediate.push(f),setInterval:()=>1,clearInterval:()=>{},
 top:()=>0,settop:()=>{},checkstack:()=>true,identity:()=>{},
 roster:()=>({complete:true,armies:[]}),readyArmy:()=>{throw Error('army_not_ready_for_details')},
 createHuntObserver:()=>({install:()=>{},close:()=>{},state:()=>({})})};
vm.createContext(context);vm.runInContext(SOURCE,context);
function tick(){hook.onEnter([1]);hook.onLeave();while(immediate.length)immediate.shift()();}
tick();context.rpc.exports.enqueue({id:'r1',operation:'ready',args:{army_id:7}});tick();
assert.equal(closed,false);
assert.equal(messages.at(-1).kind,'request_rejected');
assert.equal(messages.at(-1).reason,'army_not_ready_for_details');
context.rpc.exports.enqueue({id:'r2',operation:'roster',args:{}});tick();
assert.equal(messages.at(-1).kind,'request_result');
assert.equal(messages.at(-1).id,'r2');
'''
    result = subprocess.run(['node', '-e', 'const SOURCE=' + json.dumps(source) + ';\n' + harness],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_passive_javascript_projection_does_not_access_active_troops_morale_or_heroes():
    source = Path('src/sgz_agent/hunt_observer.js').read_text(encoding='utf-8')
    harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
let stack=[{entity:{eid:99,army:{hero_slots:[{troops:123}]},shiqi:{shiqi:80},owner:{role_id:44},
  fighter:{enemy_aoi_id:1234,is_fighting:true},army_unit:{finish_success:false}}}],readKeys=[];
function at(i){return stack[i>0?i-1:stack.length+i];}
const context={top:()=>stack.length,settop:(l,n)=>{stack.length=n},
  type:(l,i)=>{let v=at(i);return v===undefined?0:typeof v==='boolean'?1:typeof v==='number'?3:typeof v==='string'?4:5},
  scalar:(l,i)=>at(i),boolean:(l,i)=>at(i)?1:0,
  get:(l,i,k)=>{readKeys.push(k);stack.push(at(i)[k]);}};
vm.createContext(context);vm.runInContext(SOURCE,context);
let result=vm.runInContext("heoRead(1,1,HEO_DEFINITIONS.find(d=>d.id===7715).shape,{nodes:800})",context);
result=JSON.parse(JSON.stringify(result));
assert.equal(result.entity.fighter.enemy_aoi_id,1234);
assert.equal(result.entity.fighter.is_fighting,true);
assert.equal(result.entity.army_unit.finish_success,false);
assert.equal(stack.length,1);
for(const forbidden of ['army','hero_slots','troops','shiqi','owner','cur_shiqi','energy'])
  assert.equal(readKeys.includes(forbidden),false,forbidden);
'''
    result = subprocess.run(['node', '-e', 'const SOURCE=' + json.dumps(source) + ';\n' + harness],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_passive_refill_projections_capture_pool_identity_and_outgoing_targets_only():
    source = Path('src/sgz_agent/hunt_observer.js').read_text(encoding='utf-8')
    harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
let stack=[],readKeys=[];
function at(i){return stack[i>0?i-1:stack.length+i];}
const context={top:()=>stack.length,settop:(l,n)=>{stack.length=n},
  type:(l,i)=>{let v=at(i);return v===undefined?0:typeof v==='boolean'?1:typeof v==='number'?3:typeof v==='string'?4:5},
  scalar:(l,i)=>at(i),boolean:(l,i)=>at(i)?1:0,pushnil:()=>stack.push(undefined),
  get:(l,i,k)=>{readKeys.push(k);stack.push(at(i)[k]);},
  next:(l,i)=>{let table=at(i),previous=stack.pop(),keys=Object.keys(table).map(Number);
    let p=previous===undefined?0:keys.indexOf(previous)+1;
    if(p>=keys.length)return 0;stack.push(keys[p],table[keys[p]]);return 1;}};
vm.createContext(context);vm.runInContext(SOURCE,context);
function project(id,payload){stack=[payload];readKeys=[];
  const result=vm.runInContext(`heoRead(1,1,HEO_DEFINITIONS.find(d=>d.id===${id}).shape,{nodes:800})`,context);
  assert.equal(stack.length,1);return JSON.parse(JSON.stringify(result));}
let request=project(902,{army_id:7,hero_id_list:{1:101,2:102},hero_troop_list:{1:9000,2:8000},
  cons_index:3,heroes:{troops:999999},cur_shiqi:12});
assert.deepEqual(request,{army_id:7,hero_id_list:[101,102],hero_troop_list:[9000,8000],cons_index:3});
assert.equal(readKeys.includes('heroes'),false);assert.equal(readKeys.includes('cur_shiqi'),false);
assert.deepEqual(project(1091,{result:true,army_id:7}),{result:true,army_id:7});
assert.deepEqual(project(1703,{city_id:10,cur_troops:4200,cons_index:2,cur_shiqi:44}),
  {city_id:10,cur_troops:4200,cons_index:2});
for(const id of [1751,1760,1701,1706]) assert.equal(vm.runInContext(`HEO_DEFINITIONS.some(d=>d.id===${id})`,context),true);
assert.deepEqual(project(7709,{army_id:7,pos:{row:1,col:2},cur_shiqi:44}),{army_id:7,pos:{row:1,col:2}});
'''
    result = subprocess.run(['node', '-e', 'const SOURCE=' + json.dumps(source) + ';\n' + harness],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_ready_runtime_projects_only_guarded_raw_morale_parameters():
    source = Path('src/sgz_agent/hunt_runtime.js').read_text(encoding='utf-8')
    harness = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
let stack=[],reads=[],phase='idle',race=false;
const status={cur_shiqi_cost_percell:1,cur_shiqi_max:120,cur_shiqi_reco_speed:3,
  shiqi_begin_count_time:123,finish_success:true,enemy_army_id:888,cur_shiqi:120};
const army={status},mgr={army_id_tbl:{7:army}};
function at(i){return stack[i>0?i-1:stack.length+i];}
const context={OPTIONS:{generation:'test-generation'},rpc:{exports:{}},
  client:{getExportByName:()=>1},lifecycle:{attach:()=>{},state:()=>({}),close:()=>Promise.resolve({})},
  createHuntObserver:()=>({install:()=>{},close:()=>{},state:()=>({})}),
  top:()=>stack.length,settop:(l,n)=>{stack.length=n},
  type:(l,i)=>{let v=at(i);return v===undefined?0:typeof v==='number'?3:typeof v==='string'?4:5},
  get:(l,i,k)=>{stack.push(at(i)[k]);},rawi:(l,i,k)=>{stack.push(at(i)[k]);return at(-1)?5:0;},
  module:()=>{stack.push(mgr);return stack.length;},
  value:(l,i,k)=>{reads.push(k);return at(i)[k]??null;},
  statusOnly:()=>({phase}),isReady:r=>r.phase==='idle',
  readyArmy:()=>{if(phase!=='idle')throw Error('army_not_ready_for_details');
    if(race)phase='fighting';return {heroes:[],morale:120,army:{army_id:7}};}};
vm.createContext(context);vm.runInContext(SOURCE,context);
const read=()=>vm.runInContext("runtimeRead(1,{operation:'ready',args:{army_id:7}})",context);
let data=JSON.parse(JSON.stringify(read()));
assert.deepEqual(data.morale_parameters,{cur_shiqi_cost_percell:1,cur_shiqi_max:120,
  cur_shiqi_reco_speed:3,shiqi_begin_count_time:123,source:'own_army.status'});
assert.deepEqual(reads,['cur_shiqi_cost_percell','cur_shiqi_max','cur_shiqi_reco_speed','shiqi_begin_count_time']);
assert.equal(stack.length,0);assert.equal(Object.hasOwn(context.OPTIONS,'army_id'),false);
reads=[];phase='fighting';assert.throws(read,/army_not_ready/);assert.equal(reads.length,0);
phase='idle';race=true;assert.throws(read,/army_not_ready/);assert.equal(reads.length,0);
assert.equal(stack.length,0);assert.equal(Object.hasOwn(context.OPTIONS,'army_id'),false);
'''
    result = subprocess.run(['node', '-e', 'const SOURCE=' + json.dumps(source) + ';\n' + harness],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
