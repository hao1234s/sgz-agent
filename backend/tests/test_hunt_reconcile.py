"""Historical receipt uncertainty survives a separately proven terminal fence."""
from copy import deepcopy
import json

import pytest

from sgz_agent.hunt_attack import AttackJournal


IDENTITY = {'world': 'w', 'role_id': '42', 'region': 'r',
            'api_server_id': 's', 'api_season': 1}


def fixtures(store, receipt_status='unknown'):
    plan = {'world': 'w', 'army_id': '364067', 'position': [558, 1527],
            'base_id': '11183', 'created_at': 100, 'defenders_verified': True,
            'target': {'id': '2511112', 'monster_id': 123, 'x': 587, 'y': 1550,
                       'use_code': 1, 'conf_id': 10, 'observed_at': 100},
            'heroes': [{'id': str(i)} for i in (1, 2, 3)]}
    record = store.prepare('request', 'w', plan)
    store.claim(record['id'])
    receipt = {'status': receipt_status, 'sent': True,
               'reason': 'acknowledgement_unconfirmed',
               'events': [{'name': 'cs_army_cmd.response', 'data': {'result': True},
                           'identity': deepcopy(IDENTITY)}]}
    store.finish(record['id'], receipt)
    record = store.get(record['id'])
    t = record['claimed_at']
    witness = {'identity': deepcopy(IDENTITY), 'army': {
        'army_id': 364067, 'army_type': 1, 'hero_ids': [1, 2, 3],
        'observed_at': t + 100, 'status_codes': [19], 'army_status': 19,
        'system_status': 19, 'is_tying': True, 'position': [558, 1527],
        'dispatch_position': [558, 1527], 'dispatch_build_id': 11183,
        'dispatch_entered_at': 50, 'at_dispatch': True,
        'movement_end_time': 0, 'movement_total_time': 0, 'enemy_army_id': None,
        'fight_position': None, 'command': None, 'destination': None}}
    reports = [{'id': i, 'group_id': '123', 'pos': [587, 1550], 'player_id': 42,
                'result': 1, 'hero_ids': [1, 2, 3], 'primary_hero_id': 1,
                'battle_time': t + 10} for i in (1001, 1002, 1003)]
    return record, witness, reports, t + 101


def reconcile(*args, **kwargs):
    from sgz_agent.hunt_reconcile import reconcile_attack
    return reconcile_attack(*args, **kwargs)


def test_terminal_reconciliation_releases_both_fences_without_changing_old_receipt(tmp_path):
    with AttackJournal(tmp_path) as store:
        record, witness, reports, now = fixtures(store)
        original = store.db.execute('SELECT result FROM attacks').fetchone()[0]
        evidence = reconcile(store, record['id'], identity=IDENTITY,
                             status_witness=witness, reports=reports, now=now)
        assert evidence['historical_receipt_status'] == 'unknown'
        assert evidence['terminal_basis'] == 'three_matching_victories_and_return'
        assert store.get(record['id'])['status'] == 'reconciled'
        assert store.db.execute('SELECT result FROM attacks').fetchone()[0] == original
        assert store.pending('w') == []
        assert store.claim(record['id']) is False
        assert store.prepare('request', 'w', record['plan'])['id'] == record['id']
        replacement = deepcopy(record['plan'])
        replacement['army_id'] = '364068'
        assert store.prepare('new-army', 'w', replacement)['id'] != record['id']
    with AttackJournal(tmp_path) as store:
        saved = json.loads(store.db.execute('SELECT evidence FROM attack_reconciliations').fetchone()[0])
        assert saved == evidence
        assert store.get(record['id'])['result']['status'] == 'unknown'


def test_idle_or_elapsed_time_alone_cannot_retire_unknown_attack(tmp_path):
    with AttackJournal(tmp_path) as store:
        record, witness, _, now = fixtures(store)
        for reports in (None, [], [{'result': 1}]):
            with pytest.raises(ValueError, match='terminal'):
                reconcile(store, record['id'], identity=IDENTITY,
                          status_witness=witness, reports=reports, now=now)
        assert store.get(record['id'])['status'] == 'unknown'
        assert len(store.pending('w')) == 1


@pytest.mark.parametrize('patch', [
    {'army_id': 364068}, {'army_type': 2}, {'hero_ids': [4, 5, 6]},
    {'observed_at': 1}, {'observed_at': float('nan')},
    {'status_codes': [25]}, {'system_status': 25}, {'army_status': None},
    {'movement_end_time': 2}, {'movement_total_time': True},
    {'position': [559, 1527]}, {'dispatch_build_id': 99},
    {'dispatch_entered_at': None}, {'at_dispatch': False},
    {'enemy_army_id': 123}, {'fight_position': [587, 1550]}, {'command': 12},
    {'enemy_army_id': False}, {'command': False}, {'hero_ids': [True, 2, 3]},
])
def test_ambiguous_or_nonfinal_current_state_keeps_fence(tmp_path, patch):
    with AttackJournal(tmp_path) as store:
        record, witness, reports, now = fixtures(store)
        witness['army'].update(patch)
        with pytest.raises(ValueError, match='status'):
            reconcile(store, record['id'], identity=IDENTITY,
                      status_witness=witness, reports=reports, now=now)
        assert len(store.pending('w')) == 1


@pytest.mark.parametrize('key,value', [('world', 'other'), ('role_id', '43'),
    ('region', 'other'), ('api_server_id', 'other'), ('api_season', 2)])
def test_current_and_historical_identity_must_match(tmp_path, key, value):
    with AttackJournal(tmp_path) as store:
        record, witness, reports, now = fixtures(store)
        witness['identity'][key] = value
        with pytest.raises(ValueError, match='identity'):
            reconcile(store, record['id'], identity=IDENTITY,
                      status_witness=witness, reports=reports, now=now)
        assert len(store.pending('w')) == 1


@pytest.mark.parametrize('patch', [{'group_id': '124'}, {'pos': [588, 1550]},
    {'player_id': 43}, {'result': 2}, {'hero_ids': [4, 5, 6]},
    {'primary_hero_id': 2}, {'battle_time': 1}, {'id': 0},
    {'hero_ids': [True, 2, 3]}, {'primary_hero_id': True}])
def test_unrelated_or_incomplete_report_evidence_keeps_unknown(tmp_path, patch):
    with AttackJournal(tmp_path) as store:
        record, witness, reports, now = fixtures(store)
        reports[1].update(patch)
        with pytest.raises(ValueError, match='terminal'):
            reconcile(store, record['id'], identity=IDENTITY,
                      status_witness=witness, reports=reports, now=now)
        assert store.get(record['id'])['result']['status'] == 'unknown'
        assert len(store.pending('w')) == 1


def test_recorded_victory_still_requires_fresh_return_status(tmp_path):
    with AttackJournal(tmp_path) as store:
        record, witness, reports, now = fixtures(store, 'accepted')
        result = store.get(record['id'])['result']
        result.update(encounter_complete=True, departure_verified=True, reports=reports,
            target_identity_witness={'entity_id': '2511112', 'monster_id': 123,
                'x': 587, 'y': 1550, 'use_code': 1, 'conf_id': 10, 'observed_at': 100})
        store.db.execute('UPDATE attacks SET result=?', (json.dumps(result),))
        store.db.commit()
        stale = deepcopy(witness)
        stale['army']['observed_at'] = now - 31
        with pytest.raises(ValueError, match='status'):
            reconcile(store, record['id'], identity=IDENTITY, status_witness=stale, now=now)
        assert len(store.pending('w')) == 1
        assert reconcile(store, record['id'], identity=IDENTITY,
                         status_witness=witness, now=now)['historical_receipt_status'] == 'accepted'


def rebound_fixtures(store):
    record, witness, _, now = fixtures(store)
    pair = []
    for index in range(2):
        row = deepcopy(witness['army'])
        row.update(army_id=347989, dispatch_build_id=10848, observed_at=now - 4 + index * 2,
                   destination=[558, 1527])
        pair.append({'identity': deepcopy(IDENTITY),
            'roster': {'complete': True, 'armies': [row], 'observed_at': row['observed_at']},
            'runtime_final': {'generation': 'read-' + str(index), 'phase': 'closed',
                             'fault': None, 'cleanup_fault': None, 'bridge_retained': False},
            'client_session': {'pid': 10, 'created_at': 90.0}})
    return record, pair, now


def rebound(*args, **kwargs):
    from sgz_agent.hunt_reconcile import reconcile_rebound_attack
    return reconcile_rebound_attack(*args, **kwargs)


def test_two_complete_rebound_rosters_retire_only_current_force_uncertainty(tmp_path):
    with AttackJournal(tmp_path) as store:
        record, pair, now = rebound_fixtures(store)
        original = store.db.execute('SELECT result FROM attacks').fetchone()[0]
        evidence = rebound(store, record['id'], identity=IDENTITY, witnesses=pair, now=now)
        assert evidence['terminal_basis'] == 'historical_outcome_unknown_current_force_inactive'
        assert evidence['historical_outcome'] == 'unknown'
        assert evidence['mapped_army_id'] == '347989'
        assert evidence['mapped_base_id'] == '10848'
        assert evidence['historical_army_id'] == '364067'
        assert store.get(record['id'])['status'] == 'reconciled'
        assert store.db.execute('SELECT result FROM attacks').fetchone()[0] == original
        assert store.pending('w') == []
        assert store.claim(record['id']) is False


@pytest.mark.parametrize('mutation', [
    'incomplete', 'old_id_present', 'duplicate_id', 'overlapping_hero', 'changed_order',
    'new_id_changed', 'new_base_changed', 'moving', 'draw', 'enemy', 'command',
    'not_at_base', 'wrong_base_position', 'wrong_historical_position', 'other_destination',
    'stale', 'same_time', 'too_close', 'same_generation', 'other_process',
    'other_process_start', 'open_runtime', 'runtime_fault', 'retained_bridge', 'other_identity',
])
def test_rebound_requires_unique_consistent_fresh_authoritative_inactivity(tmp_path, mutation):
    with AttackJournal(tmp_path) as store:
        record, pair, now = rebound_fixtures(store)
        second = pair[1]; roster = second['roster']; row = roster['armies'][0]
        if mutation == 'incomplete': roster['complete'] = False
        elif mutation == 'old_id_present':
            roster['armies'].append({**deepcopy(row), 'army_id': 364067, 'hero_ids': [-1, -1, -1]})
        elif mutation == 'duplicate_id':
            roster['armies'].append({**deepcopy(row), 'hero_ids': [-1, -1, -1]})
        elif mutation == 'overlapping_hero':
            roster['armies'].append({**deepcopy(row), 'army_id': 99, 'hero_ids': [1, -1, -1]})
        elif mutation == 'changed_order': row['hero_ids'] = [2, 1, 3]
        elif mutation == 'new_id_changed': row['army_id'] = 347990
        elif mutation == 'new_base_changed': row['dispatch_build_id'] = 10849
        elif mutation == 'moving': row['movement_total_time'] = 5
        elif mutation == 'draw': row['status_codes'] = [25]
        elif mutation == 'enemy': row['enemy_army_id'] = 123
        elif mutation == 'command': row['command'] = 12
        elif mutation == 'not_at_base': row['at_dispatch'] = False
        elif mutation == 'wrong_base_position': row['dispatch_position'] = [559, 1527]
        elif mutation == 'wrong_historical_position':
            row.update(position=[559, 1527], dispatch_position=[559, 1527], destination=[559, 1527])
        elif mutation == 'other_destination': row['destination'] = [587, 1550]
        elif mutation == 'stale':
            pair[0]['roster']['observed_at'] = pair[0]['roster']['armies'][0]['observed_at'] = now - 31
        elif mutation in ('same_time', 'too_close'):
            roster['observed_at'] = row['observed_at'] = pair[0]['roster']['observed_at'] + (0 if mutation == 'same_time' else 1)
        elif mutation == 'same_generation': second['runtime_final']['generation'] = 'read-0'
        elif mutation == 'other_process': second['client_session']['pid'] = 11
        elif mutation == 'other_process_start': second['client_session']['created_at'] = 91.0
        elif mutation == 'open_runtime': second['runtime_final']['phase'] = 'running'
        elif mutation == 'runtime_fault': second['runtime_final']['fault'] = 'lost'
        elif mutation == 'retained_bridge': second['runtime_final']['bridge_retained'] = True
        elif mutation == 'other_identity': second['identity']['role_id'] = '43'
        with pytest.raises(ValueError, match='reconciliation'):
            rebound(store, record['id'], identity=IDENTITY, witnesses=pair, now=now)
        assert store.get(record['id'])['status'] == 'unknown'
        assert len(store.pending('w')) == 1
