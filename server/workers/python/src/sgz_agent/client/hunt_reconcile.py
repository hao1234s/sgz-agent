"""Retire an attack fence using terminal proof, preserving its original receipt.

Idle getters are a current safety check, never a historical outcome witness.
Historical victory requires the existing three-defender victory join. A separate
session-rebind path can prove only that the unique successor force is currently
inactive at its historical return position, retaining unknown past outcomes.
Callers own read authorization: this module performs no native or report reads.
"""
from copy import deepcopy
import json
import math


_IDENTITY_KEYS = ('world', 'role_id', 'region', 'api_server_id', 'api_season')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _positive(value):
    return (type(value) is int and value > 0 or
            isinstance(value, str) and value.isascii() and value.isdigit() and int(value) > 0)


def _clear_id(value):
    return value is None or type(value) is int and value == 0


def _identity(value):
    if not isinstance(value, dict) or any(k not in value for k in _IDENTITY_KEYS):
        raise ValueError('reconciliation_identity_invalid')
    if (any(not isinstance(value[k], str) or not value[k].strip()
            for k in ('world', 'role_id', 'region', 'api_server_id'))
            or not _positive(value['role_id'])
            or type(value['api_season']) is not int or value['api_season'] <= 0):
        raise ValueError('reconciliation_identity_invalid')
    return {k: value[k] for k in _IDENTITY_KEYS}


def _status(record, witness, expected, now, *, base_destination=False):
    if not isinstance(witness, dict) or _identity(witness.get('identity')) != expected:
        raise ValueError('reconciliation_identity_mismatch')
    row = witness.get('army')
    if not isinstance(row, dict):
        raise ValueError('reconciliation_status_invalid')
    p = record['plan']
    observed = row.get('observed_at')
    codes = row.get('status_codes')
    required = ('enemy_army_id', 'fight_position', 'command', 'destination')
    if (not _number(now) or not _number(observed) or not _number(record['claimed_at'])
            or not record['claimed_at'] <= observed <= now or now - observed > 30
            or str(row.get('army_id')) != record['army_id']
            or type(row.get('army_type')) is not int or row['army_type'] != 1
            or row.get('hero_ids') != [int(h['id']) for h in p['heroes']]
            or any(type(h) is not int for h in row['hero_ids'])
            or not isinstance(codes, list) or len(codes) != 1
            or type(codes[0]) is not int or codes[0] not in (1, 3, 19, 43)
            or type(row.get('army_status')) is not int or row['army_status'] != codes[0]
            or type(row.get('system_status')) is not int or row['system_status'] != codes[0]
            or any(not _number(row.get(k)) or row[k] != 0
                   for k in ('movement_end_time', 'movement_total_time'))
            or row.get('position') != p['position']
            or row.get('dispatch_position') != p['position']
            or str(row.get('dispatch_build_id')) != p['base_id']
            or row.get('at_dispatch') is not True
            or not _number(row.get('dispatch_entered_at'))
            or not 0 < row['dispatch_entered_at'] <= observed
            or any(k not in row for k in required)
            or not _clear_id(row['enemy_army_id'])
            or row['fight_position'] is not None
            or not _clear_id(row['command'])
            or not (row['destination'] is None or
                    base_destination and row['destination'] == row['dispatch_position'])):
        raise ValueError('reconciliation_status_not_final')
    return row


def _terminal(record, reports, proof, role_id, post_time):
    p = record['plan']; target = p['target']
    if proof is None and _positive(target.get('monster_id')):
        proof = {'entity_id': target['id'], 'monster_id': target['monster_id'],
                 **{k: target.get(k) for k in ('x', 'y', 'use_code', 'conf_id', 'observed_at')}}
    if (not isinstance(proof, dict) or p.get('defenders_verified') is not True
            or p.get('defenders', {}).get('count', 3) != 3
            or str(proof.get('entity_id')) != str(target['id'])
            or [proof.get('x'), proof.get('y')] != [target['x'], target['y']]
            or proof.get('use_code') != 1 or proof.get('conf_id') != 10
            or not _positive(proof.get('monster_id'))
            or not _number(proof.get('observed_at')) or not _number(p.get('created_at'))
            or not 0 <= p['created_at'] - proof['observed_at'] <= 30
            or (target.get('monster_id') is not None
                and str(target['monster_id']) != str(proof['monster_id']))):
        raise ValueError('reconciliation_terminal_target_unproven')
    if (not isinstance(reports, list) or len(reports) != 3
            or any(not isinstance(r, dict) or not _positive(r.get('id')) for r in reports)
            or len({str(r['id']) for r in reports}) != 3):
        raise ValueError('reconciliation_terminal_waves_incomplete')
    heroes = [int(h['id']) for h in p['heroes']]
    for r in reports:
        when = r.get('battle_time')
        if (str(r.get('group_id')) != str(proof['monster_id'])
                or r.get('pos') != [target['x'], target['y']]
                or type(r.get('player_id')) is not int or r['player_id'] != int(role_id)
                or type(r.get('result')) is not int or r['result'] != 1
                or r.get('hero_ids') != heroes or any(type(h) is not int for h in r['hero_ids'])
                or type(r.get('primary_hero_id')) is not int or r['primary_hero_id'] != heroes[0]
                or not _number(when) or not record['claimed_at'] <= when <= post_time):
            raise ValueError('reconciliation_terminal_report_mismatch')
    return proof


def reconcile_attack(journal, attack_id, *, identity, status_witness, now,
                     reports=None, target_witness=None):
    """Atomically store terminal evidence and release only this attack's fences.

    ``journal`` is an open AttackJournal. ``status_witness`` contains ``identity``
    and the unnormalized non-troop ``army`` statusOnly row, observed <=30s ago.
    Supply already-authorized report evidence, or use a stored verified victory.
    The original result JSON is never rewritten, including unknown acceptance.
    No readiness, refill, quarantine or execution capability is enabled here.
    """
    expected = _identity(identity)
    # The transaction covers validation and retirement: concurrent receipt
    # changes cannot be overwritten with evidence for an earlier record.
    with journal.db:
        journal.db.execute('BEGIN IMMEDIATE')
        record = journal.get(attack_id)
        old = record['result'] or {}
        if record['status'] not in ('unknown', 'accepted') or old.get('sent') is not True:
            raise ValueError('reconciliation_transition_invalid')
        historical = [e['identity'] for e in old.get('events', [])
                      if isinstance(e, dict) and 'identity' in e]
        if (record['world'] != expected['world'] or not historical
                or any(_identity(i) != expected for i in historical)):
            raise ValueError('reconciliation_historical_identity_unbound')
        row = _status(record, status_witness, expected, now)
        if reports is None and old.get('encounter_complete') is True:
            reports = old.get('reports')
            if target_witness is None:
                target_witness = old.get('target_identity_witness')
        proof = _terminal(record, reports, target_witness, expected['role_id'], row['observed_at'])
        evidence = deepcopy({'attack_id': attack_id, 'identity': expected,
            'historical_receipt_status': old.get('status'), 'historical_state': record['status'],
            'terminal_basis': 'three_matching_victories_and_return',
            'target_identity_witness': proof, 'reports': reports,
            'status_witness': status_witness, 'reconciled_at': now,
            'readiness_revalidated': False})
        journal.db.execute('CREATE TABLE IF NOT EXISTS attack_reconciliations ('
                           'attack_id TEXT PRIMARY KEY REFERENCES attacks(id), evidence TEXT NOT NULL)')
        journal.db.execute('INSERT INTO attack_reconciliations VALUES (?,?)',
            (attack_id, json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False)))
        changed = journal.db.execute("UPDATE attacks SET status='reconciled' WHERE id=? AND status=?",
                                    (attack_id, record['status'])).rowcount
        if changed != 1:
            raise ValueError('reconciliation_transition_invalid')
    return evidence


def _rebound_roster(record, witness, expected, now):
    """Validate the collector envelope and the whole own-army manager snapshot."""
    if not isinstance(witness, dict) or _identity(witness.get('identity')) != expected:
        raise ValueError('reconciliation_identity_mismatch')
    final = witness.get('runtime_final', {})
    session = witness.get('client_session', {})
    roster = witness.get('roster', {})
    if (not isinstance(final, dict) or not isinstance(session, dict) or not isinstance(roster, dict)
            or not {'generation', 'phase', 'fault', 'cleanup_fault', 'bridge_retained'} <= final.keys()
            or not isinstance(final['generation'], str) or not final['generation']
            or final['phase'] != 'closed' or final['fault'] is not None
            or final['cleanup_fault'] is not None or final['bridge_retained'] is not False
            or type(session.get('pid')) is not int or session['pid'] <= 0
            or not _number(session.get('created_at')) or session['created_at'] <= 0
            or roster.get('complete') is not True or not _number(roster.get('observed_at'))
            or not _number(now) or not 0 <= now - roster['observed_at'] <= 30
            or session['created_at'] > roster['observed_at']
            or not isinstance(roster.get('armies'), list) or not 1 <= len(roster['armies']) <= 100):
        raise ValueError('reconciliation_roster_provenance_invalid')
    ids, positive_heroes = set(), set()
    expected_heroes = [int(h['id']) for h in record['plan']['heroes']]
    if len(expected_heroes) != 3 or len(set(expected_heroes)) != 3 or any(h <= 0 for h in expected_heroes):
        raise ValueError('reconciliation_historical_formation_invalid')
    candidates = []
    for row in roster['armies']:
        if (not isinstance(row, dict) or type(row.get('army_id')) is not int or row['army_id'] <= 0
                or row['army_id'] in ids or not isinstance(row.get('hero_ids'), list)
                or any(type(h) is not int for h in row['hero_ids'])
                or not _number(row.get('observed_at'))
                or not record['claimed_at'] <= row['observed_at'] <= roster['observed_at']
                or roster['observed_at'] - row['observed_at'] > 1):
            raise ValueError('reconciliation_roster_invalid')
        ids.add(row['army_id'])
        for hero in row['hero_ids']:
            if hero > 0:
                if hero in positive_heroes:
                    raise ValueError('reconciliation_roster_duplicate_hero')
                positive_heroes.add(hero)
        if row['hero_ids'] == expected_heroes:
            candidates.append(row)
    if int(record['army_id']) in ids or len(candidates) != 1:
        raise ValueError('reconciliation_roster_rebind_ambiguous')
    row = candidates[0]
    if type(row.get('dispatch_build_id')) is not int or row['dispatch_build_id'] <= 0:
        raise ValueError('reconciliation_status_base_invalid')
    mapped = deepcopy(record)
    mapped['army_id'] = str(row['army_id'])
    mapped['plan']['base_id'] = str(row['dispatch_build_id'])
    _status(mapped, {'identity': expected, 'army': row}, expected, now, base_destination=True)
    return row


def reconcile_rebound_attack(journal, attack_id, *, identity, witnesses, now):
    """Retire a stale session reservation without resolving unknown acceptance.

    The trusted collector must wrap two complete ``roster`` runtime results only
    after clean closure. Each envelope carries identity, runtime_final and the
    observed client_session {pid, created_at}. Separate runtime generations must
    observe the same client process and unique exact ordered hero formation,
    >=2 seconds apart, with both observations <=30 seconds old. The old army ID
    must be absent from both full rosters. Consistent no-command at-base state
    proves current force inactivity; it does not prove old victory/completion.
    """
    expected = _identity(identity)
    if not isinstance(witnesses, list) or len(witnesses) != 2:
        raise ValueError('reconciliation_two_rosters_required')
    with journal.db:
        journal.db.execute('BEGIN IMMEDIATE')
        record = journal.get(attack_id)
        old = record['result'] or {}
        if (record['status'] not in ('unknown', 'accepted') or old.get('sent') is not True
                or not _number(record['claimed_at'])):
            raise ValueError('reconciliation_transition_invalid')
        historical = [e['identity'] for e in old.get('events', [])
                      if isinstance(e, dict) and 'identity' in e]
        if (record['world'] != expected['world'] or not historical
                or any(_identity(i) != expected for i in historical)):
            raise ValueError('reconciliation_historical_identity_unbound')
        rows = [_rebound_roster(record, w, expected, now) for w in witnesses]
        first, second = witnesses
        if (first['runtime_final']['generation'] == second['runtime_final']['generation']
                or first['client_session'] != second['client_session']
                or second['roster']['observed_at'] - first['roster']['observed_at'] < 2
                or rows[1]['observed_at'] - rows[0]['observed_at'] < 2
                or rows[0]['army_id'] != rows[1]['army_id']
                or rows[0]['dispatch_build_id'] != rows[1]['dispatch_build_id']):
            raise ValueError('reconciliation_roster_pair_inconsistent')
        evidence = deepcopy({'attack_id': attack_id, 'identity': expected,
            'historical_receipt_status': old.get('status'), 'historical_state': record['status'],
            'historical_outcome': old.get('result', 'unknown'),
            'terminal_basis': 'historical_outcome_unknown_current_force_inactive',
            'historical_army_id': record['army_id'], 'historical_base_id': record['plan']['base_id'],
            'mapped_army_id': str(rows[1]['army_id']),
            'mapped_base_id': str(rows[1]['dispatch_build_id']),
            'roster_witnesses': witnesses, 'reconciled_at': now, 'readiness_revalidated': False})
        journal.db.execute('CREATE TABLE IF NOT EXISTS attack_reconciliations ('
                           'attack_id TEXT PRIMARY KEY REFERENCES attacks(id), evidence TEXT NOT NULL)')
        journal.db.execute('INSERT INTO attack_reconciliations VALUES (?,?)',
            (attack_id, json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False)))
        changed = journal.db.execute("UPDATE attacks SET status='reconciled' WHERE id=? AND status=?",
                                    (attack_id, record['status'])).rowcount
        if changed != 1:
            raise ValueError('reconciliation_transition_invalid')
    return evidence
