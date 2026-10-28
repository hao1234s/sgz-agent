"""Release returned army claims for an explicit target switch, retaining history.

The caller owns the data lease and collects status-only evidence. This module
does not read the game, decide victory, or release the old target/spawn fences.
"""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

from .hunt_reconcile import _identity, _number, _clear_id


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _point(value):
    return (isinstance(value, list) and len(value) == 2
            and all(type(v) is int and 1 <= v <= 1620 for v in value))


def _clean(witness, expected):
    if not isinstance(witness, dict) or _identity(witness.get('identity')) != expected:
        raise ValueError('retarget_identity_mismatch')
    final, session = witness.get('runtime_final'), witness.get('client_session')
    if (not isinstance(final, dict) or not isinstance(session, dict)
            or not isinstance(final.get('generation'), str) or not final['generation'].strip()
            or final.get('phase') != 'closed' or final.get('bridge_retained') is not False
            or 'fault' not in final or final['fault'] is not None
            or 'cleanup_fault' not in final or final['cleanup_fault'] is not None
            or type(session.get('pid')) is not int or session['pid'] <= 0
            or not _number(session.get('created_at')) or session['created_at'] <= 0):
        raise ValueError('retarget_runtime_provenance_invalid')
    return final, session


def _inactive_roster(record, witness, expected, now):
    _final, session = _clean(witness, expected)
    roster = witness.get('roster')
    if (not isinstance(roster, dict) or roster.get('complete') is not True
            or not _number(now) or not _number(roster.get('observed_at'))
            or not 0 <= now-roster['observed_at'] <= 30
            or session['created_at'] > roster['observed_at']
            or not isinstance(roster.get('armies'), list) or not 1 <= len(roster['armies']) <= 100):
        raise ValueError('retarget_complete_fresh_roster_required')
    ids, heroes, selected = set(), set(), []
    for row in roster['armies']:
        if (not isinstance(row, dict) or type(row.get('army_id')) is not int or row['army_id'] <= 0
                or row['army_id'] in ids or not isinstance(row.get('hero_ids'), list)
                or any(type(h) is not int for h in row['hero_ids'])
                or not _number(row.get('observed_at'))
                or not record['claimed_at'] <= row['observed_at'] <= roster['observed_at']
                or roster['observed_at']-row['observed_at'] > 1):
            raise ValueError('retarget_roster_identity_invalid')
        ids.add(row['army_id'])
        for hero in row['hero_ids']:
            if hero > 0:
                if hero in heroes: raise ValueError('retarget_roster_duplicate_hero')
                heroes.add(hero)
        if str(row['army_id']) == record['army_id']: selected.append(row)
    if len(selected) != 1: raise ValueError('retarget_same_army_required')
    row, plan = selected[0], record['plan']
    expected_heroes = [int(h['id']) for h in plan['heroes']]
    position = plan.get('dispatch_position') if plan.get('launch_mode') == 'stationary_field' else plan['position']
    codes = row.get('status_codes')
    if (len(expected_heroes) != 3 or len(set(expected_heroes)) != 3 or any(h <= 0 for h in expected_heroes)
            or row['hero_ids'] != expected_heroes or type(row.get('army_type')) is not int or row['army_type'] != 1
            or not isinstance(codes, list) or len(codes) != 1 or type(codes[0]) is not int
            or codes[0] not in (1, 3, 19, 43)
            or type(row.get('army_status')) is not int or row['army_status'] != codes[0]
            or type(row.get('system_status')) is not int or row['system_status'] != codes[0]
            or any(not _number(row.get(k)) or row[k] != 0 for k in ('movement_end_time','movement_total_time'))
            or not _point(position) or row.get('position') != position or row.get('dispatch_position') != position
            or type(row.get('dispatch_build_id')) is not int or str(row['dispatch_build_id']) != plan['base_id']
            or row.get('at_dispatch') is not True or not _number(row.get('dispatch_entered_at'))
            or not 0 < row['dispatch_entered_at'] <= row['observed_at']
            or any(k not in row for k in ('command','destination','enemy_army_id','fight_position'))
            or not _clear_id(row['command']) or row['destination'] is not None
            or not _clear_id(row['enemy_army_id']) or row['fight_position'] is not None):
        raise ValueError('retarget_current_force_not_inactive_at_base')
    # is_tying is deliberately not interpreted: the three current status getters
    # and explicit no-command/no-combat conjunction establish current inactivity.
    return row


def _field_base(record, owned, expected, session, now):
    _clean(owned, expected)
    raw = owned.get('bases')
    if (owned['client_session'] != session or not isinstance(raw, dict)
            or not _number(raw.get('observed_at')) or not 0 <= now-raw['observed_at'] <= 30
            or session['created_at'] > raw['observed_at'] or not isinstance(raw.get('bases'), list)):
        raise ValueError('retarget_owned_base_provenance_invalid')
    plan = record['plan']
    matches = [b for b in raw['bases'] if isinstance(b, dict) and b.get('id') == plan['base_id']]
    if (len(matches) != 1 or matches[0].get('position') != plan.get('dispatch_position')
            or matches[0].get('source') not in ('client_lua_owned_city','client_lua_owned_build')
            or matches[0].get('creating') is True or matches[0].get('destroying') is True):
        raise ValueError('retarget_field_current_owned_base_unproven')


def _spawn(record):
    t = record['plan']['target']
    if not _number(t.get('end_time')): raise ValueError('retarget_old_spawn_unbound')
    return _encode([t['x'], t['y'], t['use_code'], t['conf_id'], float(t['end_time'])])


def _dispatch_path(journal):
    main = next((r[2] for r in journal.db.execute('PRAGMA database_list') if r[1] == 'main'), None)
    if not main: raise ValueError('retarget_dispatch_database_required')
    path = Path(main).resolve().with_name('hunt-dispatches.sqlite3')
    if not path.is_file(): raise ValueError('retarget_dispatch_database_required')
    return path


def _batch_binding(journal, record):
    path = _dispatch_path(journal)
    with closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True)) as db:
        matches = [(rid, world, snapshot) for rid, world, snapshot in db.execute(
            'SELECT request_id,world,snapshot FROM dispatch_batches')
            if 'dispatch:'+hashlib.sha256(_encode([rid,record['army_id']]).encode()).hexdigest() == record['request_id']]
        if len(matches) != 1 or matches[0][1] != record['world']:
            raise ValueError('retarget_old_batch_unbound')
        request, _world, snapshot = matches[0]
        rows = [a for a in json.loads(snapshot)['armies'] if a.get('army_id') == record['army_id']]
        if (len(rows) != 1 or rows[0].get('status') not in ('sent','unknown')
                or rows[0].get('receipt', {}).get('command_id') != record['id']
                or rows[0].get('target', {}).get('id') != record['target_id']):
            raise ValueError('retarget_old_batch_receipt_unbound')
    return dict(world=record['world'], request_id=request, army_id=record['army_id'],
        formation='heroes:'+','.join(str(int(h['id'])) for h in record['plan']['heroes']),
        target_id=record['target_id'], spawn=_spawn(record))


def _release_army(journal, binding):
    path = _dispatch_path(journal)
    with closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        for kind, resource in [('target',binding['target_id']),('spawn',binding['spawn'])]:
            owner = db.execute('SELECT request_id,army_id FROM dispatch_reservations WHERE world=? AND kind=? AND resource=?',
                (binding['world'],kind,resource)).fetchone()
            if owner != (binding['request_id'],binding['army_id']):
                raise ValueError('retarget_old_target_reservation_unbound')
        for kind, resource in [('army',binding['army_id']),('formation',binding['formation'])]:
            # A later owner's row can never match this original request.
            db.execute('DELETE FROM dispatch_reservations WHERE world=? AND kind=? AND resource=? AND request_id=? AND army_id=?',
                (binding['world'],kind,resource,binding['request_id'],binding['army_id']))


def reconcile_returned_army_for_retarget(journal, attack_id, *, identity, authorization,
                                       witnesses, owned_bases=None, now):
    return _reconcile_returned_army(journal,attack_id,identity=identity,authorization=authorization,
        witnesses=witnesses,owned_bases=owned_bases,now=now,prior_conf_id=10,purpose='retarget')


def reconcile_returned_army_for_next_round(journal, attack_id, *, identity, authorization,
                                         witnesses, owned_bases=None, now):
    """Explicitly continue profile 9 after accepted departure and proven return."""
    if (not isinstance(authorization,dict) or authorization.get('action')!='continue_round'
            or not isinstance(authorization.get('request_id'),str)
            or not authorization['request_id'].strip()):
        raise ValueError('next_round_explicit_authorization_required')
    return _reconcile_returned_army(journal,attack_id,identity=identity,authorization=authorization,
        witnesses=witnesses,owned_bases=owned_bases,now=now,prior_conf_id=9,purpose='next_round')


def _reconcile_returned_army(journal, attack_id, *, identity, authorization,
                            witnesses, owned_bases, now, prior_conf_id, purpose):
    """Retire an accepted returned force for the explicitly authorized next task.

    Two clean complete rosters must show the exact same ordered formation at its
    historically verified base. Field launches additionally require a fresh owned
    base read at the observed deployment reference; no old endpoint is invented.
    The attack transaction retains its target before releasing the army index.
    The separate batch transaction deletes only original army/formation rows.
    Identical evidence can finish that second step after a crash.
    """
    expected = _identity(identity)
    if (not isinstance(authorization, dict) or authorization.get('world') != expected['world']
            or type(authorization.get('target_conf_id')) is not int or authorization['target_conf_id'] != 9
            or not isinstance(authorization.get('statement'), str) or not authorization['statement'].strip()):
        raise ValueError('retarget_explicit_world_authorization_required')
    supplied = dict(identity=expected, authorization=authorization, witnesses=witnesses,
                    owned_bases=owned_bases, reconciled_at=now)
    digest = hashlib.sha256(_encode(supplied).encode()).hexdigest()
    with journal.db:
        journal.db.execute('BEGIN IMMEDIATE')
        journal.db.execute('CREATE TABLE IF NOT EXISTS attack_army_retargets(attack_id TEXT PRIMARY KEY,evidence TEXT NOT NULL)')
        record = journal.get(attack_id)
        saved = journal.db.execute('SELECT evidence FROM attack_army_retargets WHERE attack_id=?',(attack_id,)).fetchone()
        if record['status'] == 'retired_returned':
            if saved is None or json.loads(saved[0]).get('evidence_digest') != digest:
                raise ValueError('retarget_evidence_conflict')
            evidence = json.loads(saved[0])
        else:
            old, plan = record['result'] or {}, record['plan']
            historical = [e['identity'] for e in old.get('events',[]) if isinstance(e,dict) and 'identity' in e]
            if (record['world'] != expected['world'] or record['status'] != 'accepted'
                    or old.get('status') != 'accepted' or old.get('sent') is not True
                    or old.get('departure_verified') is not True or not _number(record['claimed_at'])
                    or type(plan.get('target',{}).get('conf_id')) is not int or plan['target']['conf_id'] != prior_conf_id
                    or type(plan['target'].get('use_code')) is not int or plan['target']['use_code'] != 1
                    or not historical or any(_identity(i) != expected for i in historical)):
                raise ValueError('retarget_accepted_departure_required')
            if not isinstance(witnesses,list) or len(witnesses) != 2:
                raise ValueError('retarget_two_rosters_required')
            rows = [_inactive_roster(record,w,expected,now) for w in witnesses]
            first, second = witnesses
            if (first['runtime_final']['generation'] == second['runtime_final']['generation']
                    or first['client_session'] != second['client_session']
                    or second['roster']['observed_at']-first['roster']['observed_at'] < 2
                    or rows[1]['observed_at']-rows[0]['observed_at'] < 2):
                raise ValueError('retarget_roster_pair_inconsistent')
            if plan.get('launch_mode') == 'stationary_field':
                _field_base(record,owned_bases,expected,second['client_session'],now)
            binding = _batch_binding(journal,record)
            original = journal.db.execute('SELECT result FROM attacks WHERE id=?',(attack_id,)).fetchone()[0]
            evidence = deepcopy({**supplied,'attack_id':attack_id,'evidence_digest':digest,
                'historical_outcome':'unknown','historical_receipt_status':'accepted',
                'terminal_basis':'two_clean_current_inactive_rosters_explicit_'+purpose,
                'readiness_revalidated':False,'original_result_sha256':hashlib.sha256(original.encode()).hexdigest(),
                'dispatch_binding':binding})
            try:
                journal.db.execute('INSERT INTO retained_attack_targets VALUES(?,?,?,?)',
                    (record['world'],record['target_id'],attack_id,binding['spawn']))
            except sqlite3.IntegrityError as error:
                raise ValueError('retarget_target_fence_conflict') from error
            journal.db.execute('INSERT INTO attack_army_retargets VALUES(?,?)',(attack_id,_encode(evidence)))
            changed = journal.db.execute("UPDATE attacks SET status='retired_returned' WHERE id=? AND status='accepted'",(attack_id,)).rowcount
            if changed != 1: raise ValueError('retarget_transition_invalid')
    _release_army(journal,evidence['dispatch_binding'])
    return evidence
