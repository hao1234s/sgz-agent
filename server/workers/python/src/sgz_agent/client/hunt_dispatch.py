"""Durable, single-round dispatch of existing regular armies.

The adapter owns live readiness, identity, cost and departure verification. This
journal freezes the selected roster and claims each send before calling it. A
request is never resumed or resent: replay only returns the durable snapshot.
Reservations intentionally outlive the round when departure is verified or its
outcome is unknown; finishing a dispatch round does not finish a battle.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from .hunt_policy import search_radius, target_profile


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _position(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(type(n) is int and 1 <= n <= 1620 for n in value))


class DispatchCoordinator:
    """No scheduler, battle wait, recruitment or automatic retry is provided."""

    def __init__(self, config, client, clock=time.time):
        self.config, self.client, self.clock = config, client, clock
        directory = Path(config.data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'hunt-dispatches.sqlite3'
        with self._connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS dispatch_batches(
                    request_id TEXT PRIMARY KEY, world TEXT NOT NULL,
                    selection TEXT NOT NULL, snapshot TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dispatch_reservations(
                    world TEXT NOT NULL, kind TEXT NOT NULL, resource TEXT NOT NULL,
                    request_id TEXT NOT NULL, army_id TEXT NOT NULL,
                    PRIMARY KEY(world,kind,resource));
            ''')

    @contextmanager
    def _connect(self):
        # Connections belong to one operation, so threads/processes share only
        # SQLite's transactional locks, never a Python connection or mutex.
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def _read(self, db, request_id):
        row = db.execute('SELECT snapshot FROM dispatch_batches WHERE request_id=?',
                         (request_id,)).fetchone()
        return json.loads(row['snapshot']) if row else None

    def _save(self, db, result):
        db.execute('UPDATE dispatch_batches SET snapshot=? WHERE request_id=?',
                   (_encode(result), result['request_id']))

    def status(self, request_id=None):
        """Read receipts without touching the native client or changing claims."""
        with self._connect() as db:
            if request_id is not None:
                result = self._read(db, request_id)
                if result is None:
                    raise ValueError('dispatch_not_found')
                return self._summarize(result)
            return {'batches': [self._summarize(json.loads(row['snapshot']))
                    for row in db.execute('SELECT snapshot FROM dispatch_batches ORDER BY rowid')]}

    def _summarize(self, result):
        counts = dict(selected=len(result['armies']), sent=0, unknown=0, blocked=0,
                      skipped=0, unavailable=0, pending=0, prepared=0)
        for row in result['armies']:
            counts[row['status']] += 1
        result['counts'] = counts
        if result.get('missing'):
            result['status'] = 'unavailable'
        elif counts['unknown']:
            result['status'] = 'unknown'
        elif counts['pending'] or counts['prepared']:
            result['status'] = 'in_progress'
        elif counts['sent'] and counts['sent'] == counts['selected']:
            result['status'] = 'sent'
        elif counts['sent']:
            result['status'] = 'partial'
        else:
            result['status'] = 'blocked'
        return result

    def _existing(self, world, request_id, selection):
        with self._connect() as db:
            row = db.execute('SELECT world,selection,snapshot FROM dispatch_batches WHERE request_id=?',
                             (request_id,)).fetchone()
        if row:
            if row['world'] != world or row['selection'] != _encode(selection):
                raise ValueError('request_conflict')
            return self._summarize(json.loads(row['snapshot']))
        return None

    def dispatch(self, world, request_id, army_ids=None):
        if not _text(world):
            raise ValueError('world_required')
        if not _text(request_id):
            raise ValueError('request_id_required')
        if army_ids is not None and (not isinstance(army_ids, list) or not army_ids
                or any(not _text(a) for a in army_ids) or len(set(army_ids)) != len(army_ids)):
            raise ValueError('invalid_army_ids')
        selection = sorted(army_ids) if army_ids is not None else 'all_regular_armies'
        previous = self._existing(world, request_id, selection)
        if previous is not None:
            return previous
        radius = search_radius(self.config, world=world)
        profile = target_profile(self.config, world=world)
        capability = self.client.capabilities()
        missing = [] if capability.get('available') is True else (
            capability.get('missing') or ['dispatch_unavailable'])
        selected = []
        if not missing:
            roster = self.client.roster_status()
            if roster.get('world') != world:
                raise RuntimeError('world_mismatch')
            if roster.get('complete') is not True:
                raise RuntimeError('roster_incomplete')
            rows = roster.get('armies')
            if (not isinstance(rows, list) or any(not isinstance(a, dict) or not _text(a.get('id')) for a in rows)
                    or len({a['id'] for a in rows}) != len(rows)):
                raise RuntimeError('invalid_or_duplicate_army_identity')
            if any(a.get('world', world) != world for a in rows):
                raise RuntimeError('world_mismatch')
            regular = {a['id']: a for a in rows if a.get('kind') == 'regular'
                       and a.get('formed') is True and _text(a.get('home_city_id'))}
            ids = sorted(regular) if army_ids is None else selection
            if not ids or any(a not in regular for a in ids):
                raise ValueError('no_eligible_armies')
            selected = [regular[a] for a in ids]
        result = dict(request_id=request_id, world=world, selection=selection,
                      created_at=self.clock(), search_radius=radius, target_profile=profile,
                      missing=missing, armies=[])
        for row in selected:
            result['armies'].append(dict(army_id=row['id'], stable_key=row.get('stable_key'),
                phase=row.get('phase', 'unknown'), home_city_id=row['home_city_id'],
                status='pending', reason=None, target=None, receipt=None,
                excluded_candidates=0, search_started_at=None, search_finished_at=None,
                prepared_at=None, send_started_at=None, finished_at=None, search_diagnostics={}))
        if missing and army_ids is not None:
            result['armies'] = [dict(army_id=a, status='unavailable', reason='capabilities_missing',
                                     target=None, receipt=None) for a in selection]
        # Exactly one concurrent caller becomes this batch's runner. Losing
        # callers return its snapshot and cannot perform candidate/detail reads.
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._read(db, request_id) is not None:
                winner = False
            else:
                db.execute('INSERT INTO dispatch_batches VALUES(?,?,?,?)',
                           (request_id, world, _encode(selection), _encode(result)))
                winner = True
        if not winner:
            return self._existing(world, request_id, selection)
        if missing:
            return self.status(request_id)
        plans = []
        # Prepare the round independently for every ready army before sending
        # any of them. Adapter.execute must revalidate a prepared plan's freshness.
        for army in selected:
            identity = army['id']
            if army.get('phase') not in {'idle', 'stationed'}:
                self._update(request_id, identity, status='skipped', reason='army_not_ready_phase',finished_at=self.clock())
                continue
            if not _position(army.get('position')):
                self._update(request_id, identity, status='blocked', reason='current_position_unknown',finished_at=self.clock())
                continue
            try:
                if not self._reserve_army(world, request_id, army):
                    self._update(request_id, identity, status='blocked', reason='army_reserved:no_resend',finished_at=self.clock())
                    continue
                target = self._find_target(world,request_id,army,radius,profile)
                if target is None:
                    raise RuntimeError('no_unreserved_verified_target')
                self._update(request_id, identity, target=target)
                plan = self.client.prepare(deepcopy(army), deepcopy(target))
                if not self._plan_valid(plan, world, identity, target, radius, profile):
                    raise RuntimeError('prepared_identity_or_return_base_mismatch')
                self._update(request_id, identity, status='prepared', reason='prepared_not_sent',
                             return_base_id=plan['return_base_id'],prepared_at=self.clock())
                plans.append((identity, plan))
            except Exception as error:
                self._update(request_id, identity, status='blocked', reason=f'{type(error).__name__}:{error}',
                             finished_at=self.clock())
                self._release(request_id, identity)
        for identity, plan in plans:
            # The only boundary after which the command could have escaped.
            # Committed before native entry, including when execute raises or
            # this process dies. This request can never enter execute again.
            self._update(request_id, identity, status='unknown', reason='send_claimed:no_resend',
                         claimed_at=self.clock(),send_started_at=self.clock())
            command_id = 'dispatch:' + hashlib.sha256(_encode([request_id, identity]).encode()).hexdigest()
            target_id = plan['target']['id']
            try:
                receipt = self.client.execute(plan, command_id)
                matching = (isinstance(receipt, dict) and receipt.get('world') == world
                            and receipt.get('army_id') == identity
                            and receipt.get('target_id') == target_id)
                if (matching and receipt.get('status') in {'applied', 'accepted', 'sent'}
                        and receipt.get('sent') is True and receipt.get('departure_verified') is True):
                    self._update(request_id, identity, status='sent', reason='departure_verified', receipt=receipt)
                elif matching and receipt.get('sent') is False and receipt.get('status') in {'rejected', 'not_applied'}:
                    self._update(request_id, identity, status='blocked', reason='verified_not_sent', receipt=receipt)
                    self._release(request_id, identity)
                else:
                    self._update(request_id, identity, reason='departure_unknown:no_resend', receipt=receipt)
            except Exception as error:
                self._update(request_id, identity, reason=f'execute_unknown:no_resend:{type(error).__name__}:{error}')
            self._update(request_id,identity,finished_at=self.clock())
        return self.status(request_id)

    def _find_target(self,world,request_id,army,radius,profile):
        identity=army['id']
        factory=getattr(self.client,'candidate_batches',None)
        streaming=callable(factory)
        diagnostics=dict(source='candidate_batches' if streaming else 'candidates',
                         windows=0,candidates=0,excluded=0,reserved=0,duplicates=0)
        self._update(request_id,identity,search_started_at=self.clock(),search_diagnostics=diagnostics)
        batches=None
        seen=set()
        try:
            batches=factory(army) if streaming else [self.client.candidates(army)]
            for candidates in batches:
                if not isinstance(candidates,list):raise RuntimeError('invalid_candidates')
                diagnostics['windows']+=1
                diagnostics['candidates']+=len(candidates)
                selected=None
                now=self.clock()
                valid=[target for target in candidates if self._candidate_valid(target,army,now,radius,profile)]
                diagnostics['excluded']+=len(candidates)-len(valid)
                for target in valid:
                    if target['id'] in seen:
                        diagnostics['duplicates']+=1
                        continue
                    seen.add(target['id'])
                    if self._reserve_target(world,request_id,identity,target):
                        selected=target
                        break
                    diagnostics['reserved']+=1
                self._update(request_id,identity,excluded_candidates=diagnostics['excluded'],
                             search_diagnostics=diagnostics)
                if selected is not None:return selected
            return None
        finally:
            try:
                close=getattr(batches,'close',None)
                if callable(close):close()
            finally:
                self._update(request_id,identity,search_finished_at=self.clock(),
                             search_diagnostics=diagnostics,excluded_candidates=diagnostics['excluded'])

    def _update(self, request_id, identity, **changes):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            result = self._read(db, request_id)
            row = next(a for a in result['armies'] if a['army_id'] == identity)
            row.update(changes)
            self._save(db, result)

    def _reserve_army(self, world, request_id, army):
        resources = [('army', army['id'])]
        if _text(army.get('stable_key')):
            resources.append(('formation', army['stable_key']))
        return self._reserve(world, request_id, army['id'], resources)

    def _reserve_target(self, world, request_id, army_id, target):
        spawn = _encode([target['x'], target['y'], target['use_code'], target['conf_id'], float(target['end_time'])])
        return self._reserve(world, request_id, army_id, [('target', target['id']), ('spawn', spawn)])

    def _reserve(self, world, request_id, army_id, resources):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for kind, resource in resources:
                row = db.execute('SELECT request_id,army_id FROM dispatch_reservations WHERE world=? AND kind=? AND resource=?',
                                 (world, kind, resource)).fetchone()
                if row and (row['request_id'], row['army_id']) != (request_id, army_id):
                    return False
            for kind, resource in resources:
                db.execute('INSERT OR IGNORE INTO dispatch_reservations VALUES(?,?,?,?,?)',
                           (world, kind, resource, request_id, army_id))
            return True

    def _release(self, request_id, army_id):
        with self._connect() as db:
            db.execute('DELETE FROM dispatch_reservations WHERE request_id=? AND army_id=?', (request_id, army_id))

    def _candidate_valid(self, target, army, now, radius=70, profile=None):
        conf_id = 10 if profile is None else profile['conf_id']
        return (isinstance(target, dict) and _text(target.get('id'))
                and type(target.get('use_code')) is int and target['use_code'] == 1
                and type(target.get('conf_id')) is int and target['conf_id'] == conf_id
                and _position([target.get('x'), target.get('y')])
                and _number(target.get('observed_at')) and 0 <= now - target['observed_at'] <= 30
                and _number(target.get('end_time')) and target['end_time'] > now
                and math.dist(army['position'], [target['x'], target['y']]) <= radius)

    def _plan_valid(self, plan, world, identity, target, radius=70, profile=None):
        if not isinstance(plan, dict) or plan.get('world') != world or plan.get('army_id') != identity:
            return False
        actual = plan.get('target')
        profile_valid = (plan.get('target_profile') == profile if profile is not None and profile['conf_id'] == 9
                         else plan.get('target_profile', profile) == profile)
        return (isinstance(actual, dict) and _text(plan.get('return_base_id'))
                and profile_valid
                and plan.get('search_radius', 70) == radius
                and all(actual.get(k) == target[k] for k in ('id', 'x', 'y', 'use_code', 'conf_id', 'end_time')))
