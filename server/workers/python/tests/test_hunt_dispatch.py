"""Offline dispatch protocol tests: no game or native runtime is accessed."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace
import threading
import json

import pytest


def coordinator(tmp_path, client):
    from sgz_agent.hunt_dispatch import DispatchCoordinator
    return DispatchCoordinator(SimpleNamespace(data_dir=tmp_path, identity={}), client, clock=lambda: 1000)


def army(identity, **changes):
    return dict(id=identity, kind='regular', formed=True, phase='idle',
                position=[500, 500], home_city_id='home', **changes)


def giant(identity, **changes):
    return dict(id=identity, x=510, y=500, use_code=1, conf_id=10,
                observed_at=999, end_time=2000, **changes)


class Client:
    def __init__(self):
        self.rows = [army('a'), army('b')]
        self.targets = [giant('g1'), {**giant('g2'), 'x': 520}]
        self.calls = []
        self.block = set()
        self.receipt = None

    def capabilities(self):
        self.calls.append(('capabilities',))
        return dict(available=True, missing=[])

    def roster_status(self):
        self.calls.append(('roster',))
        return dict(world='w', complete=True, armies=deepcopy(self.rows))

    def candidates(self, row):
        self.calls.append(('candidates', row['id']))
        return deepcopy(self.targets)

    def prepare(self, row, target):
        self.calls.append(('prepare', row['id'], target['id']))
        if row['id'] in self.block:
            raise RuntimeError('stamina_insufficient')
        return dict(world='w', army_id=row['id'], target=deepcopy(target), return_base_id='home')

    def execute(self, plan, request_id):
        self.calls.append(('execute', plan['army_id'], plan['target']['id'], request_id))
        if isinstance(self.receipt, BaseException):
            raise self.receipt
        return self.receipt or dict(world='w', army_id=plan['army_id'],
            target_id=plan['target']['id'], status='applied', sent=True, departure_verified=True)


def sent(client):
    return [call for call in client.calls if call[0] == 'execute']


def states(result):
    return {row['army_id']: row for row in result['armies']}


def test_dispatch_sends_all_existing_regular_armies_to_distinct_targets(tmp_path):
    client = Client()
    client.rows += [{**army('empty'), 'formed': False}, {**army('aux'), 'kind': 'auxiliary'}]
    result = coordinator(tmp_path, client).dispatch('w', 'round')
    assert result['status'] == 'sent'
    assert result['counts']['sent'] == 2
    assert set(states(result)) == {'a', 'b'}
    assert {(row[1], row[2]) for row in sent(client)} == {('a', 'g1'), ('b', 'g2')}
    assert len({row[3] for row in sent(client)}) == 2
    assert client.calls.index(('prepare', 'b', 'g2')) < next(i for i, c in enumerate(client.calls) if c[0] == 'execute')


def test_blocked_army_does_not_prevent_other_army_dispatch(tmp_path):
    client = Client()
    client.block.add('a')
    result = coordinator(tmp_path, client).dispatch('w', 'round')
    assert states(result)['a']['status'] == 'blocked'
    assert states(result)['b']['status'] == 'sent'
    assert result['status'] == 'partial'
    assert [row[1] for row in sent(client)] == ['b']


@pytest.mark.parametrize('phase', ['marching', 'fighting', 'draw', 'unknown', 'returning', 'resolved'])
def test_busy_armies_have_no_candidate_or_detail_read(tmp_path, phase):
    client = Client()
    client.rows[0]['phase'] = phase
    result = coordinator(tmp_path, client).dispatch('w', 'round')
    assert states(result)['a']['status'] == 'skipped'
    assert ('candidates', 'a') not in client.calls
    assert not any(c[:2] == ('prepare', 'a') for c in client.calls)
    assert result['counts']['sent'] == 1


def test_restart_replay_is_cached_and_never_expands_roster(tmp_path):
    client = Client()
    initial = coordinator(tmp_path, client).dispatch('w', 'round')
    client.rows.append(army('c'))
    client.calls.clear()
    recovered = coordinator(tmp_path, client).dispatch('w', 'round')
    assert recovered == initial
    assert client.calls == []
    with pytest.raises(ValueError, match='request_conflict'):
        coordinator(tmp_path, client).dispatch('other-world', 'round')
    with pytest.raises(ValueError, match='request_conflict'):
        coordinator(tmp_path, client).dispatch('w', 'round', ['a'])


def test_crash_after_durable_claim_never_resends_or_selects_extra_target(tmp_path):
    client = Client()
    client.receipt = KeyboardInterrupt('simulated process death before send')
    with pytest.raises(KeyboardInterrupt):
        coordinator(tmp_path, client).dispatch('w', 'round')
    restarted = coordinator(tmp_path, client)
    recovered = restarted.dispatch('w', 'round')
    assert states(recovered)['a']['status'] == 'unknown'
    assert recovered['counts']['sent'] == 0
    assert recovered['status'] != 'sent'
    assert len(sent(client)) == 1
    client.receipt = None
    second = restarted.dispatch('w', 'new', ['a'])
    assert states(second)['a']['status'] == 'blocked'
    assert len(sent(client)) == 1


def test_unknown_receipt_is_not_counted_as_sent(tmp_path):
    client = Client()
    client.receipt = dict(status='accepted', sent=True, departure_verified=False)
    result = coordinator(tmp_path, client).dispatch('w', 'round', ['a'])
    assert result['status'] == 'unknown'
    assert result['counts']['sent'] == 0
    assert result['counts']['unknown'] == 1


def test_targets_remain_reserved_across_batches_with_unknown_or_departed_army(tmp_path):
    client = Client()
    client.targets = [giant('only')]
    dispatch = coordinator(tmp_path, client)
    first = dispatch.dispatch('w', 'first', ['a'])
    second = dispatch.dispatch('w', 'second', ['b'])
    assert first['counts']['sent'] == 1
    assert second['counts']['blocked'] == 1
    assert len(sent(client)) == 1


def test_rebased_army_id_does_not_bypass_formation_reservation(tmp_path):
    client = Client()
    client.rows[0]['stable_key'] = 'heroes:1,2,3'
    dispatch = coordinator(tmp_path, client)
    dispatch.dispatch('w', 'first', ['a'])
    client.rows[0]['id'] = 'rebased'
    second = dispatch.dispatch('w', 'second', ['rebased'])
    assert second['counts']['blocked'] == 1
    assert len(sent(client)) == 1


def test_rebased_target_id_does_not_bypass_spawn_reservation(tmp_path):
    client = Client()
    client.targets = [giant('old')]
    dispatch = coordinator(tmp_path, client)
    dispatch.dispatch('w', 'first', ['a'])
    client.targets = [giant('rebased')]
    second = dispatch.dispatch('w', 'second', ['b'])
    assert second['counts']['blocked'] == 1
    assert len(sent(client)) == 1


def test_concurrent_duplicate_requests_send_each_army_once(tmp_path):
    client = Client()
    barrier = threading.Barrier(2)
    roster = client.roster_status
    def simultaneous_roster():
        result = roster()
        barrier.wait(timeout=5)
        return result
    client.roster_status = simultaneous_roster
    first, second = coordinator(tmp_path, client), coordinator(tmp_path, client)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda c: c.dispatch('w', 'same'), [first, second]))
    assert len(sent(client)) == 2
    assert first.status('same')['counts']['sent'] == 2
    assert all(set(states(r)) == {'a', 'b'} for r in results)


@pytest.mark.parametrize('changes', [dict(use_code=2), dict(conf_id=11), dict(x=571), dict(x=550, y=550),
    dict(x=True), dict(observed_at=900), dict(observed_at=1001), dict(end_time=1000), dict(id='')])
def test_invalid_stale_or_out_of_circle_candidates_are_excluded(tmp_path, changes):
    client = Client()
    client.targets = [{**giant('bad'), **changes}, giant('good')]
    result = coordinator(tmp_path, client).dispatch('w', 'round', ['a'])
    assert states(result)['a']['target']['id'] == 'good'
    assert states(result)['a']['excluded_candidates'] == 1
    assert sent(client)[0][2] == 'good'


@pytest.mark.parametrize('changes', [dict(world='wrong'), dict(army_id='wrong'),
    dict(target=giant('wrong')), dict(return_base_id=None)])
def test_wrong_prepared_identity_or_missing_return_base_never_executes(tmp_path, changes):
    client = Client()
    prepare = client.prepare
    client.prepare = lambda a, t: {**prepare(a, t), **changes}
    result = coordinator(tmp_path, client).dispatch('w', 'round', ['a'])
    assert result['counts']['blocked'] == 1
    assert sent(client) == []


def test_wrong_receipt_identity_remains_unknown(tmp_path):
    client = Client()
    client.receipt = dict(world='wrong', army_id='a', target_id='g1',
                          status='applied', sent=True, departure_verified=True)
    result = coordinator(tmp_path, client).dispatch('w', 'round', ['a'])
    assert result['counts']['unknown'] == 1
    assert result['counts']['sent'] == 0


@pytest.mark.parametrize('problem', ['duplicate', 'wrong_world', 'incomplete'])
def test_untrusted_roster_never_creates_dispatch(tmp_path, problem):
    client = Client()
    if problem == 'duplicate':
        client.rows.append(army('a'))
    elif problem == 'wrong_world':
        client.roster_status = lambda: dict(world='wrong', complete=True, armies=client.rows)
    else:
        client.roster_status = lambda: dict(world='w', complete=False, armies=client.rows)
    with pytest.raises((ValueError, RuntimeError)):
        coordinator(tmp_path, client).dispatch('w', 'round')
    assert sent(client) == []


def test_unavailable_capability_has_truthful_cached_result_without_native_read(tmp_path):
    client = Client()
    client.capabilities = lambda: dict(available=False, missing=['dispatch_validation'])
    result = coordinator(tmp_path, client).dispatch('w', 'round', ['a'])
    assert result['status'] == 'unavailable'
    assert result['counts']['sent'] == 0
    assert result['missing'] == ['dispatch_validation']
    assert client.calls == []


def test_prepare_cannot_change_target_coordinates_by_mutating_its_argument(tmp_path):
    client = Client()
    prepare = client.prepare
    def changed_target(row, target):
        target['x'] = 1000
        return prepare(row, target)
    client.prepare = changed_target
    result = coordinator(tmp_path, client).dispatch('w', 'round', ['a'])
    assert result['counts']['blocked'] == 1
    assert sent(client) == []


def test_rebased_target_float_expiry_does_not_bypass_spawn_reservation(tmp_path):
    client = Client()
    client.targets = [giant('old')]
    dispatch = coordinator(tmp_path, client)
    dispatch.dispatch('w', 'first', ['a'])
    client.targets = [{**giant('rebased'), 'end_time': 2000.0}]
    second = dispatch.dispatch('w', 'second', ['b'])
    assert second['counts']['blocked'] == 1
    assert len(sent(client)) == 1


def test_conflicting_receipt_status_is_never_counted_as_sent(tmp_path):
    client = Client()
    client.receipt = dict(world='w', army_id='a', target_id='g1',
                          status='rejected', sent=True, departure_verified=True)
    result = coordinator(tmp_path, client).dispatch('w', 'round', ['a'])
    assert result['counts']['unknown'] == 1
    assert result['counts']['sent'] == 0


def test_roster_with_one_mixed_world_identity_never_reads_details(tmp_path):
    client = Client()
    client.rows[0]['world'] = 'other'
    with pytest.raises(RuntimeError, match='world_mismatch'):
        coordinator(tmp_path, client).dispatch('w', 'round')
    assert not any(c[0] in {'candidates', 'prepare', 'execute'} for c in client.calls)


def test_execute_cannot_relabel_target_by_mutating_the_plan(tmp_path):
    client = Client()
    execute = client.execute
    def changed_plan(plan, request_id):
        plan['target']['id'] = 'unselected'
        return execute(plan, request_id)
    client.execute = changed_plan
    result = coordinator(tmp_path, client).dispatch('w', 'round', ['a'])
    assert states(result)['a']['target']['id'] == 'g1'
    assert result['counts']['sent'] == 0
    assert result['counts']['unknown'] == 1


@pytest.mark.parametrize('record,expected', [(None,70), ({'world':'w'},70),
    ({'world':'other','search_radius':140,'search_radius_authorization':'expand'},70),
    ({'world':'w','search_radius':140,'search_radius_authorization':'User expanded discovery'},140)])
def test_search_radius_policy_is_defaulted_and_world_bound(tmp_path, record, expected):
    from sgz_agent.hunt_policy import search_radius
    if record is not None:
        (tmp_path/'hunt-user-policy.json').write_text(json.dumps(record), encoding='utf-8')
    assert search_radius(SimpleNamespace(data_dir=tmp_path,identity={'world':'w'})) == expected


@pytest.mark.parametrize('radius,authorization', [(140,''),(140,None),(141,'expand'),
    (1000,'expand'),(140.0,'expand'),(True,'expand'),(None,'expand')])
def test_invalid_or_unauthorized_expanded_search_policy_fails_closed(tmp_path, radius, authorization):
    from sgz_agent.hunt_policy import search_radius
    (tmp_path/'hunt-user-policy.json').write_text(json.dumps(dict(world='w',search_radius=radius,
        search_radius_authorization=authorization)), encoding='utf-8')
    with pytest.raises(ValueError,match='hunt_search'):
        search_radius(SimpleNamespace(data_dir=tmp_path,identity={'world':'w'}))


def test_explicit_expansion_changes_only_discovery_circle_and_is_frozen_for_replay(tmp_path):
    (tmp_path/'hunt-user-policy.json').write_text(json.dumps(dict(world='w',search_radius=140,
        search_radius_authorization='User requested broader search')), encoding='utf-8')
    client = Client()
    client.targets = [{**giant('too_far'),'x':641}, {**giant('expanded'),'x':620}]
    prepare = client.prepare
    client.prepare = lambda a,t: {**prepare(a,t),'search_radius':140,'preview':{'path_len':1000}}
    dispatch = coordinator(tmp_path,client)
    result = dispatch.dispatch('w','expanded',['a'])
    assert result['search_radius'] == 140
    assert result['counts']['sent'] == 1
    assert states(result)['a']['target']['id'] == 'expanded'
    (tmp_path/'hunt-user-policy.json').unlink()
    client.calls.clear()
    assert dispatch.dispatch('w','expanded',['a']) == result
    assert client.calls == []


@pytest.mark.parametrize('record,conf_id',[(None,10),
    ({'world':'other','target_conf_id':9,'target_authorization':'switch'},10),
    ({'world':'w','target_conf_id':9,'target_authorization':'User requested heavy infantry'},9)])
def test_target_profile_requires_current_world_authorization_for_nine(tmp_path,record,conf_id):
    from sgz_agent.hunt_policy import target_profile
    if record is not None:
        (tmp_path/'hunt-user-policy.json').write_text(json.dumps(record),encoding='utf-8')
    profile=target_profile(SimpleNamespace(data_dir=tmp_path,identity={'world':'w'}))
    assert profile['use_code']==1 and profile['conf_id']==conf_id
    assert profile['defender_count']==(2 if conf_id==9 else 3)


@pytest.mark.parametrize('conf_id,authorization',[(9,None),(9,''),(8,'switch'),(11,'switch'),
    (9.0,'switch'),(True,'switch')])
def test_missing_target_authorization_or_other_npc_configuration_fails_closed(tmp_path,conf_id,authorization):
    from sgz_agent.hunt_policy import target_profile
    (tmp_path/'hunt-user-policy.json').write_text(json.dumps(dict(world='w',target_conf_id=conf_id,
        target_authorization=authorization)),encoding='utf-8')
    with pytest.raises(ValueError,match='hunt_target'):
        target_profile(SimpleNamespace(data_dir=tmp_path,identity={'world':'w'}))


def test_switched_target_dispatch_excludes_giants_and_replays_its_original_profile(tmp_path):
    (tmp_path/'hunt-user-policy.json').write_text(json.dumps(dict(world='w',target_conf_id=9,
        target_authorization='User requested heavy infantry')),encoding='utf-8')
    profile=dict(use_code=1,conf_id=9,display_name='重徒兵',defender_count=2)
    client=Client()
    client.targets=[giant('wrong-giant'),{**giant('wrong-kind'),'use_code':3,'conf_id':9},
                    {**giant('heavy'),'conf_id':9}]
    prepare=client.prepare
    client.prepare=lambda a,t:{**prepare(a,t),'target_profile':profile}
    dispatch=coordinator(tmp_path,client)
    result=dispatch.dispatch('w','heavy-round',['a'])
    assert result['counts']['sent']==1
    assert result['target_profile']==profile
    assert states(result)['a']['target']['id']=='heavy'
    assert states(result)['a']['excluded_candidates']==2
    (tmp_path/'hunt-user-policy.json').unlink()
    client.calls.clear()
    assert dispatch.dispatch('w','heavy-round',['a'])==result
    assert client.calls==[]


def test_stream_stops_at_first_unreserved_target_and_records_stage_timing(tmp_path):
    from sgz_agent.hunt_dispatch import DispatchCoordinator
    client=Client();clock=dict(now=1000)
    def stream(row):
        try:
            client.calls.append(('window',row['id']))
            clock['now']+=7.5
            yield [giant('first'),giant('unrelated')]
            pytest.fail('usable first window must stop wider discovery')
        finally:client.calls.append(('stream_closed',row['id']))
    client.candidate_batches=stream
    client.candidates=lambda row:pytest.fail('production stream must take precedence')
    prepare,execute=client.prepare,client.execute
    def timed_prepare(row,target):
        clock['now']+=3
        return prepare(row,target)
    def timed_execute(plan,request_id):
        clock['now']+=2
        return execute(plan,request_id)
    client.prepare,client.execute=timed_prepare,timed_execute
    dispatch=DispatchCoordinator(SimpleNamespace(data_dir=tmp_path,identity={}),client,clock=lambda:clock['now'])
    result=dispatch.dispatch('w','stream',['a']);row=states(result)['a']
    assert row['status']=='sent' and row['target']['id']=='first'
    assert row['search_started_at']==1000 and row['prepared_at']==1010.5
    assert row['send_started_at']==1010.5 and row['finished_at']==1012.5
    assert row['search_diagnostics']==dict(source='candidate_batches',windows=1,candidates=2,excluded=0,reserved=0,duplicates=0)
    assert ('stream_closed','a') in client.calls
    assert [call for call in client.calls if call[0]=='prepare']==[('prepare','a','first')]
    before=deepcopy(client.calls)
    assert dispatch.dispatch('w','stream',['a'])==result and client.calls==before


def test_reserved_stream_window_continues_until_a_distinct_target_is_prepared(tmp_path):
    client=Client();dispatch=coordinator(tmp_path,client)
    dispatch.dispatch('w','first',['a'])
    def stream(row):
        yield [giant('g1')]
        yield [giant('g1'),{**giant('g2'),'x':520}]
        pytest.fail('third window is unnecessary')
    client.candidate_batches=stream
    result=dispatch.dispatch('w','second',['b']);row=states(result)['b']
    assert row['status']=='sent' and row['target']['id']=='g2'
    assert row['search_diagnostics']['windows']==2
    assert row['search_diagnostics']['reserved']==1 and row['search_diagnostics']['duplicates']==1


def test_cancelled_stream_never_prepares_or_sends_an_army(tmp_path):
    client=Client()
    def stream(row):
        yield []
        raise RuntimeError('stopped')
    client.candidate_batches=stream
    result=coordinator(tmp_path,client).dispatch('w','cancelled',['a'])
    row=states(result)['a']
    assert row['status']=='blocked' and 'stopped' in row['reason']
    assert row['search_diagnostics']['windows']==1 and row['finished_at']==1000
    assert not any(call[0] in {'prepare','execute'} for call in client.calls)


def test_stream_excludes_invalid_and_late_candidates_before_selecting_a_target(tmp_path):
    client=Client()
    def stream(row):
        yield [{**giant('old'),'observed_at':900},{**giant('wrong'),'use_code':3}]
        yield [giant('fresh')]
    client.candidate_batches=stream
    result=coordinator(tmp_path,client).dispatch('w','fresh-stream',['a'])
    row=states(result)['a']
    assert row['target']['id']=='fresh' and row['excluded_candidates']==2
    assert row['search_diagnostics']['windows']==2 and row['search_diagnostics']['excluded']==2


def test_stream_diagnostics_count_invalid_rows_after_the_selected_candidate(tmp_path):
    client=Client()
    client.candidate_batches=lambda row:iter([[giant('fresh'),{**giant('invalid'),'use_code':3}]])
    result=coordinator(tmp_path,client).dispatch('w','diagnostics',['a'])
    row=states(result)['a']
    assert row['status']=='sent' and row['excluded_candidates']==1
    assert row['search_diagnostics']['candidates']==2 and row['search_diagnostics']['excluded']==1
