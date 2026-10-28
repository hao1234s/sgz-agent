"""Live adapter guards tested with recorded-shaped offline evidence only."""
from types import SimpleNamespace
from copy import deepcopy
import json

import pytest


def fixture(tmp_path):
    from sgz_agent.hunt_trial import build_plan, fresh_target
    identity = dict(world='w', role_id='42', region='r', api_server_id='s', api_season=1)
    config = SimpleNamespace(data_dir=tmp_path, identity=identity, executable=tmp_path/'client.exe')
    raw_army = dict(army_id=7, army_type=1, city_id=8, dispatch_build_id=8,
        hero_ids=[1, 2, 3], observed_at=99, status_codes=[1], army_status=1,
        system_status=1, movement_end_time=0, movement_total_time=0,
        position=[10, 10], dispatch_position=[10, 10], dispatch_entered_at=50,
        at_dispatch=True, is_tying=False)
    army = dict(id='7', kind='regular', formed=True, phase='idle', position=[10, 10],
        home_city_id='8', current_base_id='8', stable_key='heroes:1,2,3', raw_status=raw_army)
    inputs = dict(army=raw_army, heroes=[dict(id=str(i), is_main=i == 1, troops=9000,
        capacity=10000, stamina=120, wounded_until=0) for i in [1, 2, 3]],
        morale=120, stamina=120, morale_cost_per_cell=0.6, observed_at=99,
        orders_available=48, cost_decree=1, cost_energy=15, can_auto_return=True,
        defender_count=3, target_level=10)
    preview = dict(result=1, need_time=1005, path_len=68, need_energy=15)
    refresh = dict(cleanup_verified=True, overflow=False, cancelled=False,
        sessions=[dict(session_id=1, center=[30, 10], sent_at=99, finished_at=99.8)],
        events=[dict(name='sc_notify_refresh_aoi_content', received_at=99.5,
            data=dict(session_id=1, info_list=[dict(id=9, proto_type='add', monster=dict(
                pos=dict(row=30, col=10), use_code=1, conf_id=10, army_conf_id=30075,
                troop_type=2, end_time=1000, monter_id=123))]))])
    target = fresh_target(config, refresh, '9', [30, 10], now=100)
    plan = build_plan(config, army, target, inputs, preview, minimum_morale=0, now=100)
    plan['return_base_id'] = '8'
    final = dict(phase='closed', fault=None, cleanup_fault=None, bridge_retained=False, generation='g')
    evidence = dict(identity=deepcopy(identity), runtime_generation='g', army=army,
        plan=plan, inputs=inputs, runtime_final=final, target_refresh=refresh)
    return config, evidence


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from sgz_agent import hunt_dispatch_client as m
    config, evidence = fixture(tmp_path)
    monkeypatch.setattr(m, '_binding', lambda c: dict(identity=dict(c.identity), implementation='a', incident='none'))
    monkeypatch.setattr(m, '_controlled_readiness', lambda c: None)
    monkeypatch.setattr(m.time, 'time', lambda: 101)
    return m, config, evidence


def save(m, config, evidence):
    return m.save_dispatch_validation(config, preflight_evidence=evidence,
        authorization='User requested repair and this all-army dispatch round')


def test_dispatch_verification_is_bound_to_identity_code_and_incident(environment):
    m, config, evidence = environment
    record = save(m, config, evidence)
    assert record['scope'] == 'controlled_single_round'
    assert record['unattended_logistics'] is False
    assert m.LiveDispatchClient(config).capabilities()['available'] is True
    config.identity['world'] = 'other'
    assert m.LiveDispatchClient(config).capabilities()['available'] is False


@pytest.mark.parametrize('field,value', [('phase', 'quarantined'), ('fault', 'native error'),
    ('cleanup_fault', 'timeout'), ('bridge_retained', True), ('generation', True)])
def test_bad_native_evidence_cannot_grant_dispatch(environment, field, value):
    m, config, evidence = environment
    evidence['runtime_final'][field] = value
    with pytest.raises(ValueError, match='dispatch_verification'):
        save(m, config, evidence)


@pytest.mark.parametrize('mutation', ['identity', 'generation', 'stale_plan', 'stale_inputs',
    'unclean_refresh', 'missing_refresh', 'mismatched_target', 'mismatched_inputs',
    'formation', 'duplicate_heroes', 'missing_hero', 'nonregular', 'no_preview', 'no_return_base'])
def test_incomplete_or_unbound_preflight_cannot_grant_dispatch(environment, mutation):
    m, config, evidence = environment
    if mutation == 'identity': evidence['identity']['role_id'] = '43'
    elif mutation == 'generation': evidence['runtime_generation'] = 'other'
    elif mutation == 'stale_plan': evidence['plan']['created_at'] = 1
    elif mutation == 'stale_inputs': evidence['inputs']['observed_at'] = 1
    elif mutation == 'unclean_refresh': evidence['target_refresh']['cleanup_verified'] = False
    elif mutation == 'missing_refresh': evidence.pop('target_refresh')
    elif mutation == 'mismatched_target': evidence['plan']['target']['id'] = '10'
    elif mutation == 'mismatched_inputs': evidence['inputs']['orders_available'] = 47
    elif mutation == 'formation': evidence['army']['stable_key'] = 'heroes:2,1,3'
    elif mutation == 'duplicate_heroes': evidence['plan']['heroes'][1]['id'] = '1'
    elif mutation == 'missing_hero': evidence['plan']['heroes'].pop()
    elif mutation == 'nonregular': evidence['army']['kind'] = 'auxiliary'
    elif mutation == 'no_preview': evidence['plan'].pop('preview')
    else: evidence['plan']['return_base_id'] = None
    with pytest.raises(ValueError, match='dispatch_verification'):
        save(m, config, evidence)


@pytest.mark.parametrize('field,value', [('authorization', ''), ('verified_at', float('nan')),
    ('verified_at', 102), ('verified_at', -3600), ('preflight_evidence', None),
    ('unattended_logistics', True), ('scope', 'continuous')])
def test_malformed_saved_record_is_unavailable(environment, field, value):
    m, config, evidence = environment
    record = save(m, config, evidence)
    record[field] = value
    (config.data_dir/'hunt-dispatch-validation.json').write_text(json.dumps(record), encoding='utf-8')
    assert m.LiveDispatchClient(config).capabilities()['available'] is False


def test_saved_preflight_is_checked_at_verification_time_but_record_expires_in_one_hour(environment, monkeypatch):
    m, config, evidence = environment
    save(m, config, evidence)
    monkeypatch.setattr(m.time, 'time', lambda: 3500)
    assert m.LiveDispatchClient(config).capabilities()['available'] is True
    monkeypatch.setattr(m.time, 'time', lambda: 3702)
    assert m.LiveDispatchClient(config).capabilities()['available'] is False


def test_missing_record_fields_do_not_advertise_dispatch(environment):
    m, config, evidence = environment
    record = save(m, config, evidence)
    record.pop('authorization')
    (config.data_dir/'hunt-dispatch-validation.json').write_text(json.dumps(record), encoding='utf-8')
    assert m.LiveDispatchClient(config).capabilities()['available'] is False


def test_adapter_reprepares_pinned_target_and_formation_before_send(environment, monkeypatch):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    monkeypatch.setattr(client, '_require', lambda scope: None)
    stale = deepcopy(evidence['plan']); stale['expires_at'] = 0
    fresh = deepcopy(evidence['plan'])
    seen = []
    monkeypatch.setattr(client, '_prepare', lambda army_id, target, **kw:
        (seen.append(('prepare', army_id, target, kw)) or fresh))
    monkeypatch.setattr(m, 'execute_under_lease', lambda config, request_id, plan, stop_event:
        (seen.append(('send', plan, stop_event)) or dict(status='accepted', sent=True, departure_verified=True)))
    receipt = client.execute(stale, 'r')
    assert seen[0][3] == dict(expected_stable_key='heroes:1,2,3')
    assert seen[1] == ('send', fresh, client.stop_event)
    assert receipt['world'] == 'w' and receipt['army_id'] == '7' and receipt['target_id'] == '9'


@pytest.mark.parametrize('mutation', ['heroes', 'world', 'army_id', 'target', 'base', 'launch_mode'])
def test_repreparation_cannot_change_the_pinned_command_identity(environment, monkeypatch, mutation):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    monkeypatch.setattr(client, '_require', lambda scope: None)
    plan = evidence['plan']; fresh = deepcopy(plan)
    if mutation == 'heroes': fresh['heroes'][0]['id'] = '4'
    elif mutation == 'world': fresh['world'] = 'other'
    elif mutation == 'army_id': fresh['army_id'] = '8'
    elif mutation == 'target': fresh['target']['id'] = '10'
    elif mutation == 'base': fresh['base_id'] = fresh['return_base_id'] = 'other'
    else: fresh['launch_mode'] = 'stationary_field'
    monkeypatch.setattr(client, '_prepare', lambda *a, **kw: fresh)
    def forbidden_send(*a, **kw): pytest.fail('changed command must never enter sender')
    monkeypatch.setattr(m, 'execute_under_lease', forbidden_send)
    receipt = client.execute(plan, 'r')
    assert receipt['status'] == 'rejected' and receipt['sent'] is False


def test_prepare_binds_the_original_roster_formation(environment, monkeypatch):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    monkeypatch.setattr(client, '_require', lambda scope: None)
    seen = []
    monkeypatch.setattr(client, '_prepare', lambda *a, **kw: (seen.append(kw) or evidence['plan']))
    client.prepare(evidence['army'], evidence['plan']['target'])
    assert seen == [dict(expected_stable_key='heroes:1,2,3')]


def test_changed_roster_formation_blocks_before_native_details(environment, monkeypatch):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    changed = {**evidence['army'], 'stable_key': 'heroes:4,5,6'}
    monkeypatch.setattr(m, '_assert_external_clear', lambda *a: None)
    monkeypatch.setattr(client, 'roster_status', lambda: dict(armies=[changed]))
    def forbidden_runtime(*a, **kw): pytest.fail('changed formation must not read details')
    monkeypatch.setattr(m, 'ControlledAttackRuntime', forbidden_runtime)
    with pytest.raises(ValueError, match='formation'):
        client._prepare('7', evidence['plan']['target'], expected_stable_key='heroes:1,2,3')


def test_preflight_rejection_is_verified_unsent(environment, monkeypatch):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    monkeypatch.setattr(client, '_require', lambda scope: None)
    def blocked(*a, **kw): raise ValueError('heroes_unready')
    monkeypatch.setattr(client, '_prepare', blocked)
    result = client.execute(evidence['plan'], 'r')
    assert result['sent'] is False and result['status'] == 'rejected'


def test_stationary_field_army_gets_only_a_fresh_owned_return_reference(environment, monkeypatch):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    army = deepcopy(evidence['army'])
    army.update(phase='stationed', current_base_id=None, position=[12, 10])
    army['raw_status'].update(position=[12, 10], at_dispatch=False)
    raw = dict(cleanup_verified=True, observed_at=100, bases=[dict(id='8', position=[10, 10],
        source='client_lua_owned_build', creating=False, destroying=None)])
    calls = []
    monkeypatch.setattr(client.client, '_read', lambda op: (calls.append(op) or raw))
    prepared = client._verify_return_base(army)
    assert calls == ['bases']
    assert prepared['current_base_id'] is None
    assert prepared['return_base_id'] == '8'
    assert prepared['return_base_witness'] == dict(world='w', army_id='7', id='8',
        position=[10, 10], source='client_lua_owned_build', owned=True, observed_at=100)
    assert 'capacity_available' not in prepared['return_base_witness']


@pytest.mark.parametrize('mutation', ['unclean', 'stale', 'future', 'wrong_id', 'wrong_position',
    'duplicate', 'source', 'creating', 'destroying'])
def test_invalid_return_reference_blocks_field_preparation(environment, monkeypatch, mutation):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    army = deepcopy(evidence['army']); army.update(phase='stationed', current_base_id=None)
    raw = dict(cleanup_verified=True, observed_at=100, bases=[dict(id='8', position=[10, 10],
        source='client_lua_owned_build', creating=False, destroying=None)])
    if mutation == 'unclean': raw['cleanup_verified'] = False
    elif mutation == 'stale': raw['observed_at'] = 1
    elif mutation == 'future': raw['observed_at'] = 102
    elif mutation == 'wrong_id': raw['bases'][0]['id'] = '9'
    elif mutation == 'wrong_position': raw['bases'][0]['position'] = [11, 10]
    elif mutation == 'duplicate': raw['bases'].append(deepcopy(raw['bases'][0]))
    elif mutation == 'source': raw['bases'][0]['source'] = 'public_map'
    elif mutation == 'creating': raw['bases'][0]['creating'] = True
    else: raw['bases'][0]['destroying'] = True
    monkeypatch.setattr(client.client, '_read', lambda op: raw)
    with pytest.raises(ValueError, match='return_base'):
        client._verify_return_base(army)


def test_candidate_sampling_reaches_the_radius_boundary_without_coverage_claim(environment, monkeypatch):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    monkeypatch.setattr(client, '_require', lambda scope: None)
    seen = []
    def read(op, **args):
        seen.append((op, args))
        return dict(cleanup_verified=True, overflow=False, cancelled=False, sessions=[], events=[])
    monkeypatch.setattr(client.client, '_read', read)
    result = client.candidates({**evidence['army'], 'position': [500, 500]})
    assert result == []
    centers = [center for _, args in seen for center in args['centers']]
    assert len(centers) == 41
    assert [570, 500] in centers and [430, 500] in centers and [520, 500] in centers
    assert centers[-1] == [500, 500]
    distances = [(x-500)**2 + (y-500)**2 for x, y in centers]
    assert distances == sorted(distances, reverse=True)
    assert all(len(args['centers']) <= 8 for _, args in seen)
    assert all(args['dwell_ms'] == 1500 and args['timeout_ms'] == 20000 for _, args in seen)


def test_pinned_target_refresh_waits_for_delayed_content_before_preparing(environment, monkeypatch):
    m, config, evidence = environment
    save(m, config, evidence)
    client = m.LiveDispatchClient(config)
    monkeypatch.setattr(m, '_assert_external_clear', lambda *a: None)
    monkeypatch.setattr(m, 'morale_floor', lambda config: 0)
    monkeypatch.setattr(client, 'roster_status', lambda: dict(armies=[evidence['army']]))
    class Runtime:
        generation = 'g'
        def __init__(self, *a, **kw): pass
        def start(self): return self
        def request(self, operation):
            assert operation == 'attack_inputs'
            return evidence['inputs']
        def close(self): pass
        def state(self): return evidence['runtime_final']
    monkeypatch.setattr(m, 'ControlledAttackRuntime', Runtime)
    seen = []
    def read(operation, **arguments):
        seen.append((operation, arguments))
        return (dict(preview=evidence['plan']['preview']) if operation == 'preview'
                else evidence['target_refresh'])
    monkeypatch.setattr(client.client, '_read', read)
    plan = client._prepare('7', evidence['plan']['target'], expected_stable_key='heroes:1,2,3')
    assert plan['target']['id'] == '9'
    refresh = next(arguments for operation, arguments in seen if operation == 'search_fresh')
    assert refresh['dwell_ms'] == 2000
    assert refresh['timeout_ms'] == 10000


def test_field_validation_keeps_return_endpoint_unverified_and_requires_raw_owned_base_evidence(environment):
    from sgz_agent.hunt_trial import build_plan
    m, config, evidence = environment
    army, inputs = evidence['army'], evidence['inputs']
    army.update(phase='stationed', current_base_id=None, position=[12, 10])
    for raw in (army['raw_status'], inputs['army']):
        raw.update(status_codes=[3], army_status=3, system_status=3, at_dispatch=False,
            position=[12, 10], command=None, enemy_army_id=None, fight_position=None, destination=None)
    base_read = dict(cleanup_verified=True, observed_at=99, bases=[dict(id='8', position=[10, 10],
        source='client_lua_owned_build', creating=False, destroying=None)])
    bound = m._bind_return_base(army, base_read, 'w', 100)
    plan = build_plan(config, bound, evidence['plan']['target'], inputs,
                      evidence['plan']['preview'], minimum_morale=0, now=100)
    evidence.update(army=bound, plan=plan, return_base_read=base_read)
    record = save(m, config, evidence)
    assert record['preflight_evidence']['plan']['return_destination_verified'] is False
    assert m.LiveDispatchClient(config).capabilities()['available'] is True
    evidence.pop('return_base_read')
    with pytest.raises(ValueError, match='dispatch_verification'):
        save(m, config, evidence)


def test_only_exact_foreign_refresh_read_failure_retries_once_with_new_sessions(environment, monkeypatch):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    guards, reads = [], []
    monkeypatch.setattr(client, '_require', lambda scope: guards.append(scope))
    def read(operation, **args):
        reads.append(args)
        if len(reads) == 1: raise RuntimeError('hunt_search_foreign_refresh_observed')
        return dict(cleanup_verified=True)
    monkeypatch.setattr(client.client, '_read', read)
    result = client._search_read('7', center=[10, 10], radius=70, centers=[[10, 10]], dwell_ms=1500, timeout_ms=20000)
    assert result['cleanup_verified'] is True
    assert guards == ['read', 'read']
    assert len(reads) == 2 and reads[0]['session_seed'] != reads[1]['session_seed']


@pytest.mark.parametrize('reason,attempts', [('hunt_search_foreign_refresh_observed', 2),
    ('native_quiescence_unconfirmed:cleanup', 1), ('api_script_error:access violation', 1),
    ('hunt_client_timeout', 1), ('api_identity_mismatch', 1), ('stopped', 1),
    ('cleanup:hunt_search_foreign_refresh_observed', 1)])
def test_read_retry_is_bounded_and_never_retries_native_or_cleanup_errors(environment, monkeypatch, reason, attempts):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    reads = []
    monkeypatch.setattr(client, '_require', lambda scope: None)
    def read(*a, **kw):
        reads.append(kw)
        raise RuntimeError(reason)
    monkeypatch.setattr(client.client, '_read', read)
    with pytest.raises(RuntimeError, match=reason):
        client._search_read('7', center=[10, 10], radius=70, centers=[[10, 10]], dwell_ms=1500, timeout_ms=20000)
    assert len(reads) == attempts


def test_stop_before_read_retry_prevents_another_native_entry(environment, monkeypatch):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    monkeypatch.setattr(client, 'capabilities', lambda: dict(available=True))
    reads = []
    def read(*a, **kw):
        reads.append(kw); client.stop_event.set()
        raise RuntimeError('hunt_search_foreign_refresh_observed')
    monkeypatch.setattr(client.client, '_read', read)
    with pytest.raises(RuntimeError, match='stopped'):
        client._search_read('7', center=[10, 10], radius=70, centers=[[10, 10]], dwell_ms=1500, timeout_ms=20000)
    assert len(reads) == 1


@pytest.mark.parametrize('confirmation', ['present', 'missing', 'changed', 'unclean'])
def test_old_tentative_discovery_requires_a_fresh_matching_target_before_return(environment, monkeypatch, confirmation):
    m, config, evidence = environment
    client = m.LiveDispatchClient(config)
    monkeypatch.setattr(client, '_require', lambda scope: None)
    monkeypatch.setattr(m, 'plan_query_centers', lambda *a, **kw: [[10+i, 10] for i in range(9)])
    clock, reads = dict(now=100), []
    monkeypatch.setattr(m.time, 'time', lambda: clock['now'])
    def read(operation, **args):
        reads.append(args)
        if args['radius'] == 70:
            clock['now'] += 30
            return (deepcopy(evidence['target_refresh']) if len(reads) == 1 else
                    dict(cleanup_verified=True, sessions=[], events=[]))
        raw = deepcopy(evidence['target_refresh'])
        raw['sessions'][0].update(sent_at=160, finished_at=161.8)
        raw['events'][0]['received_at'] = 160.5
        clock['now'] = 162
        if confirmation == 'missing': raw['events'] = []
        elif confirmation == 'changed': raw['events'][0]['data']['info_list'][0]['monster']['end_time'] = 2000
        elif confirmation == 'unclean': raw['cleanup_verified'] = False
        return raw
    monkeypatch.setattr(client.client, '_read', read)
    if confirmation == 'unclean':
        with pytest.raises((ValueError, RuntimeError), match='unclean'):
            client.candidates(evidence['army'])
    else:
        result = client.candidates(evidence['army'])
        assert [row['id'] for row in result] == (['9'] if confirmation == 'present' else [])
        if result: assert result[0]['observed_at'] == 160.5
        assert len(reads) == 3 and reads[-1]['dwell_ms'] == 2000


@pytest.mark.parametrize('source', ['hunt_observer.js', 'client_api.py', 'replenish_client.py', 'hunt_policy.py', 'hunt_retarget.py'])
def test_dispatch_validation_binding_includes_runtime_observer_and_readiness_sources(tmp_path, monkeypatch, source):
    from pathlib import Path
    from sgz_agent import hunt_dispatch_client as m
    config, _ = fixture(tmp_path)
    before = m._binding(config)['implementation']
    read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda path:
        read_bytes(path) + (b'\n' if path.name == source else b''))
    assert m._binding(config)['implementation'] != before


def test_authorized_expansion_uses_local_native_bounds_and_global_discovery_circle(environment,monkeypatch):
    m,config,evidence = environment
    (config.data_dir/'hunt-user-policy.json').write_text(json.dumps(dict(world='w',search_radius=140,
        search_radius_authorization='User expanded discovery')),encoding='utf-8')
    client=m.LiveDispatchClient(config)
    monkeypatch.setattr(client,'_require',lambda scope:None)
    reads=[]
    def read(operation,**args):
        reads.append(args)
        return dict(cleanup_verified=True,sessions=[],events=[])
    monkeypatch.setattr(client.client,'_read',read)
    assert client.candidates({**evidence['army'],'position':[500,500]}) == []
    centers=[p for args in reads for p in args['centers']]
    assert len(centers)==49 and [640,500] in centers and [360,500] in centers
    assert all(args['radius']==70 and len(args['centers'])<=8 for args in reads)
    assert all((x-args['center'][0])**2+(y-args['center'][1])**2<=70**2
               for args in reads for x,y in args['centers'])
    assert client.last_candidate_diagnostics['search_radius']==140


@pytest.mark.parametrize('x,expected',[(635,['9']),(650,[])])
def test_expanded_native_sampling_filters_against_army_origin_scope(environment,monkeypatch,x,expected):
    m,config,evidence=environment
    (config.data_dir/'hunt-user-policy.json').write_text(json.dumps(dict(world='w',search_radius=140,
        search_radius_authorization='User expanded discovery')),encoding='utf-8')
    client=m.LiveDispatchClient(config)
    monkeypatch.setattr(client,'_require',lambda scope:None)
    reads=[]
    def read(operation,**args):
        reads.append(args)
        if len(reads)>1 and args['radius']==70:
            return dict(cleanup_verified=True,sessions=[],events=[])
        raw=deepcopy(evidence['target_refresh'])
        raw['sessions'][0]['center']=[640,500]
        raw['events'][0]['data']['info_list'][0]['monster']['pos']=dict(row=x,col=500)
        return raw
    monkeypatch.setattr(client.client,'_read',read)
    result=client.candidates({**evidence['army'],'position':[500,500]})
    assert [t['id'] for t in result]==expected


def test_authorized_nine_candidate_discovery_cannot_return_legacy_giants(environment,monkeypatch):
    m,config,evidence=environment
    (config.data_dir/'hunt-user-policy.json').write_text(json.dumps(dict(world='w',target_conf_id=9,
        target_authorization='User requested heavy infantry')),encoding='utf-8')
    client=m.LiveDispatchClient(config)
    monkeypatch.setattr(client,'_require',lambda scope:None)
    reads=[]
    def read(operation,**args):
        reads.append(args)
        if args['radius']==70 and len(reads)>1:
            return dict(cleanup_verified=True,sessions=[],events=[])
        raw=deepcopy(evidence['target_refresh'])
        raw['events'][0]['data']['info_list'][0]['monster']['conf_id']=9
        if args['radius']==70:
            legacy=deepcopy(raw['events'][0]['data']['info_list'][0])
            legacy['id']=10;legacy['monster']['conf_id']=10
            raw['events'][0]['data']['info_list'].append(legacy)
        return raw
    monkeypatch.setattr(client.client,'_read',read)
    targets=client.candidates(evidence['army'])
    assert [(target['id'],target['use_code'],target['conf_id']) for target in targets]==[('9',1,9)]
    assert client.last_candidate_diagnostics['target_profile']['conf_id']==9


def test_plentiful_nine_targets_use_one_near_origin_window_then_individual_fresh_confirmation(environment,monkeypatch):
    m,config,evidence=environment
    (config.data_dir/'hunt-user-policy.json').write_text(json.dumps(dict(world='w',target_conf_id=9,
        target_authorization='User requested heavy infantry')),encoding='utf-8')
    client=m.LiveDispatchClient(config)
    monkeypatch.setattr(client,'_require',lambda scope:None)
    reads=[]
    raw=deepcopy(evidence['target_refresh'])
    template=raw['events'][0]['data']['info_list'][0]
    rows=[]
    for identity,x in [(9,30),(11,31),(12,32)]:
        row=deepcopy(template);row['id']=identity;row['monster'].update(conf_id=9,pos=dict(row=x,col=10))
        rows.append(row)
    raw['events'][0]['data']['info_list']=rows
    def read(operation,**args):
        reads.append(args)
        return deepcopy(raw)
    monkeypatch.setattr(client.client,'_read',read)
    result=client.candidates(evidence['army'])
    assert [row['id'] for row in result]==['9','11','12']
    searches=[args for args in reads if args['radius']==70]
    assert len(searches)==1 and searches[0]['centers']==[[10,10],[30,10],[10,30]]
    assert len(reads)==4
    assert client.last_candidate_diagnostics['sampled_centers']==3


def test_nine_fallback_confirms_only_six_nearest_targets_before_freshness_expires(environment,monkeypatch):
    m,config,evidence=environment
    (config.data_dir/'hunt-user-policy.json').write_text(json.dumps(dict(world='w',target_conf_id=9,
        target_authorization='User requested heavy infantry')),encoding='utf-8')
    client=m.LiveDispatchClient(config)
    monkeypatch.setattr(client,'_require',lambda scope:None)
    clock,reads=dict(now=100),[]
    monkeypatch.setattr(m.time,'time',lambda:clock['now'])
    raw=deepcopy(evidence['target_refresh'])
    template=raw['events'][0]['data']['info_list'][0]
    rows=[]
    for index in range(20):
        row=deepcopy(template);row['id']=9+index
        row['monster'].update(conf_id=9,pos=dict(row=30+index,col=10));rows.append(row)
    raw['events'][0]['data']['info_list']=rows
    sample_count=0
    def read(operation,**args):
        nonlocal sample_count
        reads.append(args)
        if args['radius']==70:
            sample_count+=1
            return (deepcopy(raw) if sample_count==2 else
                    dict(cleanup_verified=True,sessions=[],events=[]))
        clock['now']+=2
        confirmed=deepcopy(raw)
        confirmed['sessions'][0].update(sent_at=clock['now']-1,finished_at=clock['now'])
        confirmed['events'][0]['received_at']=clock['now']-0.5
        return confirmed
    monkeypatch.setattr(client.client,'_read',read)
    result=client.candidates(evidence['army'])
    assert [target['id'] for target in result]==['9','10','11','12','13','14']
    assert len([args for args in reads if args['radius']==1])==6
    assert all(0<=clock['now']-target['observed_at']<=30 for target in result)


def test_candidate_stream_yields_first_near_window_without_unrelated_target_confirmation(environment,monkeypatch):
    m,config,evidence=environment
    client=m.LiveDispatchClient(config)
    monkeypatch.setattr(client,'_require',lambda scope:None)
    reads=[]
    def read(operation,**args):
        reads.append(args)
        return deepcopy(evidence['target_refresh'])
    monkeypatch.setattr(client.client,'_read',read)
    stream=client.candidate_batches(evidence['army'])
    first=next(stream);stream.close()
    assert [target['id'] for target in first]==['9']
    assert len(reads)==1 and reads[0]['radius']==70
    assert reads[0]['centers']==[[10,10],[30,10],[10,30]]
    assert reads[0]['dwell_ms']==1500
    assert client.last_candidate_diagnostics['coverage_verified'] is False


def test_candidate_stream_empty_near_window_falls_back_near_first_in_bounded_native_windows(environment,monkeypatch):
    m,config,evidence=environment
    client=m.LiveDispatchClient(config)
    monkeypatch.setattr(client,'_require',lambda scope:None)
    reads=[]
    def read(operation,**args):
        reads.append(args)
        return dict(cleanup_verified=True,sessions=[],events=[])
    monkeypatch.setattr(client.client,'_read',read)
    batches=list(client.candidate_batches({**evidence['army'],'position':[500,500]}))
    assert batches and all(batch==[] for batch in batches)
    assert reads[0]['centers']==[[500,500],[520,500],[480,500],[500,520],[500,480]]
    assert all(args['radius']<=70 and len(args['centers'])<=8 for args in reads)
    points=[point for args in reads for point in args['centers']]
    assert len(points)==len({tuple(point) for point in points})==41
    distances=[min((x-500)**2+(y-500)**2 for x,y in args['centers']) for args in reads[1:]]
    assert distances==sorted(distances)


@pytest.mark.parametrize('problem',['stale','cancelled','unclean'])
def test_candidate_stream_never_yields_stale_or_unclean_observations(environment,monkeypatch,problem):
    m,config,evidence=environment
    client=m.LiveDispatchClient(config)
    monkeypatch.setattr(client,'_require',lambda scope:None)
    raw=deepcopy(evidence['target_refresh'])
    if problem=='stale':monkeypatch.setattr(m.time,'time',lambda:140)
    elif problem=='cancelled':raw['cancelled']=True
    else:raw['cleanup_verified']=False
    monkeypatch.setattr(client.client,'_read',lambda *a,**kw:raw)
    stream=client.candidate_batches(evidence['army'])
    if problem=='stale':
        assert next(stream)==[];stream.close()
    else:
        with pytest.raises(RuntimeError,match='unclean'):next(stream)


def test_coordinator_live_adapter_stream_refreshes_only_its_selected_target(environment,monkeypatch):
    from sgz_agent.hunt_dispatch import DispatchCoordinator
    m,config,evidence=environment
    save(m,config,evidence)
    client=m.LiveDispatchClient(config)
    monkeypatch.setattr(client,'roster_status',lambda:dict(world='w',complete=True,armies=[evidence['army']]))
    monkeypatch.setattr(m,'morale_floor',lambda config:0)
    reads,executions=[],[]
    class Runtime:
        generation='g'
        def __init__(self,*a,**kw):pass
        def start(self):return self
        def request(self,operation):
            assert operation=='attack_inputs'
            return deepcopy(evidence['inputs'])
        def close(self):pass
        def state(self):return deepcopy(evidence['runtime_final'])
    monkeypatch.setattr(m,'ControlledAttackRuntime',Runtime)
    def read(operation,**arguments):
        reads.append((operation,arguments))
        if operation=='preview':return dict(preview=deepcopy(evidence['plan']['preview']))
        raw=deepcopy(evidence['target_refresh'])
        if arguments['radius']==70:
            for identity,x in [(11,31),(12,32)]:
                row=deepcopy(raw['events'][0]['data']['info_list'][0]);row['id']=identity
                row['monster']['pos']=dict(row=x,col=10)
                raw['events'][0]['data']['info_list'].append(row)
        return raw
    monkeypatch.setattr(client.client,'_read',read)
    def execute(config,request_id,plan,stop_event):
        executions.append(plan['target']['id'])
        return dict(status='accepted',sent=True,departure_verified=True)
    monkeypatch.setattr(m,'execute_under_lease',execute)
    result=DispatchCoordinator(config,client,clock=lambda:101).dispatch('w','production-stream',['7'])
    assert result['counts']['sent']==1 and executions==['9']
    searches=[arguments for operation,arguments in reads if operation=='search_fresh']
    assert [arguments['radius'] for arguments in searches]==[70,1,1]
    assert all(arguments['center']==[30,10] for arguments in searches[1:])
    assert result['armies'][0]['search_diagnostics']['windows']==1
