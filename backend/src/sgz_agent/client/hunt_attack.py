"""Evidence and durable fences for a single controlled giant attack.

This module does not enable the unattended hunt coordinator. Acknowledgement,
departure and whole-encounter completion remain separate facts.
"""
from contextlib import AbstractContextManager, closing
import json
from copy import deepcopy
import math
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .hunt_client import _positive_id, _position
from .hunt_runtime import HuntNativeRuntime
from .hunt_policy import search_radius, target_profile
from .lease import DataLease


def morale_floor(config):
    path = config.data_dir / 'hunt-user-policy.json'
    if not path.exists():
        return 100
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('world') != config.identity['world']:
        return 100
    value = record.get('min_arrival_morale')
    if (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100
            or not isinstance(record.get('authorization'), str) or not record['authorization'].strip()):
        raise ValueError('hunt_morale_policy_invalid')
    return value


def _plan_target_profile(plan):
    target = plan.get('target', {})
    if (type(target.get('use_code')) is not int or target['use_code'] != 1
            or type(target.get('conf_id')) is not int or target['conf_id'] not in (9,10)):
        raise ValueError('attack_target_profile_invalid')
    conf = target['conf_id']
    expected = {'use_code':1, 'conf_id':conf, 'display_name':'重徒兵' if conf == 9 else '巨戟士',
                'defender_count':2 if conf == 9 else 3}
    supplied = plan.get('target_profile')
    if ((supplied is None and conf != 10) or supplied is not None and _encode(supplied) != _encode(expected)):
        raise ValueError('attack_target_profile_invalid')
    return expected


def attack_request(plan):
    _plan_target_profile(plan)
    target = plan['target']
    _positive_id(target.get('id'))
    position = _position([target.get('x'), target.get('y')])
    return {'cmd': 12, 'army_id': _positive_id(plan.get('army_id')),
            'target_pos': position, 'is_auto_fallback': True,
            'path_pos': [], 'cmd_target_type': 1, 'is_occupy': False, 'ext_str': '',
            'extra_data': {'interrupt_guard': True, 'cancel_conscription': True}}


def command_receipt(request, events):
    """Untagged replies are diagnostic only; require an own-army departure."""
    traffic = [e for e in events if e.get('name') in ('cs_army_cmd', 'cs_army_cmd.response')]
    requests = [e for e in traffic if e['name'] == 'cs_army_cmd']
    responses = [e for e in traffic if e['name'] == 'cs_army_cmd.response']
    unknown = {'status': 'unknown', 'sent': bool(requests), 'reason': 'acknowledgement_unconfirmed'}
    if (len(requests) != 1 or requests[0].get('data') != request
            or type(requests[0].get('sequence')) is not int):
        return unknown
    unknown['untagged_responses'] = [e.get('data') for e in responses]
    for event in events:
        if (event.get('name') != 'sc_sync_army_status' or type(event.get('sequence')) is not int
                or event['sequence'] <= requests[0]['sequence']):
            continue
        army = event.get('data', {}).get('army_info', {})
        status = army.get('status', {})
        if (army.get('army_id') == request['army_id'] and status.get('cmd') == 12
                and status.get('status') == [2] and status.get('end_pos') == request['target_pos']
                and status.get('is_auto_fallback') is True
                and type(status.get('total_time')) in (int,float) and status['total_time'] > 0
                and type(status.get('end_time')) in (int,float) and status['end_time'] > 0):
            return {'status':'accepted', 'sent':True, 'departure_verified':True,
                    'encounter_complete':False, 'reason':'matching_army_departure',
                    'departure_event':event, 'untagged_responses':unknown['untagged_responses']}
    return unknown


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def validate_departure_witness(record, row, *, now):
    """Pure validation shared by tentative polling and durable reconciliation."""
    if not isinstance(row, dict):
        raise ValueError('departure_witness_invalid')
    plan, old = record['plan'], record['result'] or {}
    sent_at = record['claimed_at']
    observed, end, duration = (row.get(k) for k in
                              ('observed_at','movement_end_time','movement_total_time'))
    numbers = (now,sent_at,observed,end,duration)
    expected_heroes = [int(h['id']) for h in plan['heroes']]
    formation_changed = ('hero_ids' in row and (not isinstance(row['hero_ids'], list)
        or row['hero_ids'] != expected_heroes or any(type(h) is not int for h in row['hero_ids'])))
    at_origin = row.get('position') == plan['position']
    # Coordinates may still show the launch city before the first movement tick.
    # This existing exception requires complete own-force marching proof.
    origin_proven = (type(row.get('army_type')) is int and row['army_type'] == 1
        and row.get('status_codes') == [2] and all(type(v) is int for v in row['status_codes'])
        and type(row.get('army_status')) is int and row['army_status'] == 2
        and type(row.get('system_status')) is int and row['system_status'] == 2
        and isinstance(row.get('hero_ids'), list)
        and row['hero_ids'] == expected_heroes
        and all(type(h) is int for h in row['hero_ids'])
        and str(row.get('dispatch_build_id')) == plan['base_id']
        and 'enemy_army_id' in row and row['enemy_army_id'] is None
        and 'fight_position' in row and row['fight_position'] is None) if at_origin else False
    if (record['world'] != plan.get('world')
            or record['status'] not in ('unknown','accepted') or old.get('sent') is not True
            or any(type(v) not in (int,float) or not math.isfinite(v) for v in numbers)
            or not 0 <= now-observed <= 30 or observed < sent_at or end <= observed
            or abs((end-duration)-sent_at) > 10
            or duration != plan['preview']['need_time']
            or str(row.get('army_id')) != record['army_id'] or row.get('status_codes') != [2]
            or row.get('command') != 12 or row.get('auto_return') is not True
            or formation_changed or at_origin and not origin_proven
            or row.get('destination') != [plan['target']['x'],plan['target']['y']]):
        raise ValueError('departure_witness_invalid')
    _position(row.get('position'))


class AttackJournal(AbstractContextManager):
    """One-shot send claims survive restart; outstanding attacks fence both keys."""
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(Path(directory) / 'hunt-attacks.sqlite3', timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS attacks (
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, world TEXT NOT NULL,
                army_id TEXT NOT NULL, target_id TEXT NOT NULL, plan TEXT NOT NULL,
                status TEXT NOT NULL, claimed_at REAL, result TEXT, created_at REAL NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS attack_army_pending ON attacks(world,army_id)
                WHERE status IN ('prepared','submitted','accepted','unknown');
            CREATE UNIQUE INDEX IF NOT EXISTS attack_target_pending ON attacks(world,target_id)
                WHERE status IN ('prepared','submitted','accepted','unknown');
            CREATE TABLE IF NOT EXISTS retained_attack_targets (
                world TEXT, target_id TEXT, attack_id TEXT, spawn TEXT,
                PRIMARY KEY(world,target_id), UNIQUE(world,spawn));
        ''')

    def __exit__(self, *args):
        self.db.close()

    @staticmethod
    def _decode(row):
        if row is None:
            raise ValueError('attack_not_found')
        value = dict(row)
        value['plan'] = json.loads(value['plan'])
        value['result'] = json.loads(value['result']) if value['result'] else None
        return value

    def get(self, identity):
        return self._decode(self.db.execute('SELECT * FROM attacks WHERE id=?', (identity,)).fetchone())

    def pending(self, world):
        return [self._decode(r) for r in self.db.execute(
            "SELECT * FROM attacks WHERE world=? AND status IN ('prepared','submitted','accepted','unknown')", (world,))]

    def prepare(self, request_id, world, plan):
        attack_request(plan)
        if not isinstance(request_id, str) or not request_id.strip() or not world or plan.get('world') != world:
            raise ValueError('attack_identity_invalid')
        payload = _encode(plan)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            previous = self.db.execute('SELECT * FROM attacks WHERE request_id=?', (request_id,)).fetchone()
            if previous:
                if previous['world'] != world or previous['plan'] != payload:
                    raise ValueError('attack_request_conflict')
                return self._decode(previous)
            target = plan['target']
            end = target.get('end_time')
            spawn = (_encode([target['x'],target['y'],target['use_code'],target['conf_id'],float(end)])
                     if type(end) in (int,float) and math.isfinite(end) else None)
            retained = self.db.execute('SELECT 1 FROM retained_attack_targets WHERE world=? '
                'AND (target_id=? OR (? IS NOT NULL AND spawn=?)) LIMIT 1',
                (world,target['id'],spawn,spawn)).fetchone()
            if retained:
                raise RuntimeError('attack_target_retained')
            pending = self.pending(world)
            if any(r['army_id'] == plan['army_id'] for r in pending):
                raise RuntimeError('attack_army_pending')
            if any(r['target_id'] == plan['target']['id'] for r in pending):
                raise RuntimeError('attack_target_pending')
            identity = uuid4().hex
            self.db.execute('INSERT INTO attacks VALUES (?,?,?,?,?,?,?,NULL,NULL,?)',
                            (identity, request_id, world, plan['army_id'], plan['target']['id'],
                             payload, 'prepared', time.time()))
        return self.get(identity)

    def claim(self, identity):
        # This commit happens before a native sender can be entered. A crash even
        # before sending is unknown, never permission for a second call.
        with self.db:
            changed = self.db.execute("UPDATE attacks SET claimed_at=?,status='submitted' "
                                      "WHERE id=? AND claimed_at IS NULL AND status='prepared'",
                                      (time.time(), identity)).rowcount
        return changed == 1

    def finish(self, identity, receipt):
        if (receipt.get('status') not in ('accepted', 'rejected', 'unknown')
                or receipt.get('status') == 'rejected' and receipt.get('sent') is not False):
            raise ValueError('attack_receipt_invalid')
        with self.db:
            changed = self.db.execute("UPDATE attacks SET status=?,result=? WHERE id=? AND status='submitted'",
                            (receipt['status'], _encode(receipt), identity))
            if changed.rowcount != 1:
                raise ValueError('attack_receipt_transition_invalid')

    def reconcile_departure(self, identity, row, *, now):
        """A fresh own-status read may resolve a sent command; it never releases it."""
        record = self.get(identity)
        validate_departure_witness(record, row, now=now)
        old = record['result'] or {}
        receipt = {**old, 'status':'accepted','departure_verified':True,'encounter_complete':False,
                   'reason':'fresh_matching_army_departure','departure_snapshot':deepcopy(row)}
        with self.db:
            self.db.execute("UPDATE attacks SET status='accepted',result=? WHERE id=? AND status IN ('unknown','accepted')",
                            (_encode(receipt),identity))
        return receipt

    def record_victory(self, identity, *, target_witness, reports, role_id, post_status, now):
        """Record three matching wins without authorizing another attack/refill.

        Cached result headers are joined to the AOI monster identity captured in
        this command's preflight. A return witness is required in addition to all
        three wins. Eligibility, recovery and replenishment remain separate.
        """
        record = self.get(identity)
        plan, old, proof = record['plan'], record['result'] or {}, target_witness
        if plan.get('launch_mode') == 'stationary_field':
            raise ValueError('field_return_destination_unverified')
        profile = _plan_target_profile(plan)
        waves = profile['defender_count']
        t = plan['target']; observed = proof.get('observed_at')
        if (record['status'] != 'accepted' or old.get('departure_verified') is not True
                or old.get('sent') is not True or plan.get('defenders_verified') is not True
                or str(proof.get('entity_id')) != str(t['id'])
                or [proof.get('x'),proof.get('y')] != [t['x'],t['y']]
                or type(proof.get('use_code')) is not int or proof['use_code'] != profile['use_code']
                or type(proof.get('conf_id')) is not int or proof['conf_id'] != profile['conf_id']
                or type(observed) not in (int,float) or not math.isfinite(observed)
                or not 0 <= plan['created_at']-observed <= 30):
            raise ValueError('victory_target_witness_invalid')
        monster = str(_positive_id(proof.get('monster_id')))
        expected_heroes = [int(h['id']) for h in plan['heroes']]
        if (not isinstance(reports,list) or len(reports) != waves
                or len({str(r.get('id')) for r in reports}) != waves):
            raise ValueError('victory_waves_incomplete')
        post = post_status; post_time = post.get('observed_at')
        if (type(post_time) not in (int,float) or not math.isfinite(post_time)
                or not record['claimed_at'] <= post_time <= now
                or str(post.get('army_id')) != record['army_id']):
            raise ValueError('victory_return_witness_invalid')
        returning = post.get('status_codes') == [4] and post.get('destination') == plan['position']
        returned = (post.get('status_codes') in ([1],[3],[19],[43])
                    and post.get('position') == plan['position']
                    and str(post.get('dispatch_build_id')) == plan['base_id']
                    and post.get('movement_total_time') == 0 and post.get('movement_end_time') == 0)
        if not (returning or returned):
            raise ValueError('victory_return_witness_invalid')
        for r in reports:
            _positive_id(r.get('id'))
            when = r.get('battle_time')
            if (str(r.get('group_id')) != monster or r.get('pos') != [t['x'],t['y']]
                    or type(r.get('player_id')) is not int or r['player_id'] != int(role_id)
                    or type(r.get('result')) is not int or r['result'] != 1
                    or r.get('hero_ids') != expected_heroes or r.get('primary_hero_id') != expected_heroes[0]
                    or type(when) not in (int,float) or not math.isfinite(when)
                    or not record['claimed_at'] <= when <= post_time):
                raise ValueError('victory_report_mismatch')
        receipt = {**old,'encounter_complete':True,'result':'victory','completed_waves':waves,
                   'target_identity_witness':deepcopy(proof),'reports':deepcopy(reports),
                   'post_encounter_status':deepcopy(post),'automatic_return_observed':True,
                   'returned_to_base':returned,'readiness_revalidated':False,
                   'reason':'victory_confirmed_readiness_pending'}
        with self.db:
            self.db.execute("UPDATE attacks SET result=? WHERE id=? AND status='accepted'",
                            (_encode(receipt),identity))
        return receipt


class ControlledAttackRuntime(HuntNativeRuntime):
    """Explicit test-only extension of the read runtime, with an immutable plan."""
    OPERATIONS = HuntNativeRuntime.OPERATIONS | {'attack', 'attack_inputs'}

    def __init__(self, config, *, plan, controlled_verification=False, **kwargs):
        if controlled_verification is not True:
            raise ValueError('controlled_attack_authorization_required')
        attack_request(plan)
        if plan.get('world') != config.identity.get('world'):
            raise ValueError('attack_world_mismatch')
        if _plan_target_profile(plan) != target_profile(config):
            raise ValueError('attack_target_profile_not_authorized')
        radius = plan.get('search_radius', 70)
        if (type(radius) is not int or radius not in (70, 140)
                or radius > search_radius(config, world=plan['world'])):
            raise ValueError('attack_search_policy_not_authorized')
        floor = plan.get('min_morale')
        if type(floor) not in (int, float) or not math.isfinite(floor) or floor < morale_floor(config):
            raise ValueError('attack_morale_policy_not_authorized')
        self.attack_plan = deepcopy(plan)
        super().__init__(config, enabled=True, controlled_verification=True, **kwargs)

    @staticmethod
    def _arguments(operation, arguments):
        if operation in ('attack', 'attack_inputs'):
            if arguments:
                raise ValueError('controlled_attack_arguments_forbidden')
            return {}
        return HuntNativeRuntime._arguments(operation, arguments)

    def _source(self):
        source = super()._source()
        header, body = source.split('\n', 1)
        options = json.loads(header.removeprefix('const OPTIONS = ').removesuffix(';'))
        options['controlled_attack_plan'] = self.attack_plan
        marker = '// Persistent fixed-operation queue.'
        if body.count(marker) != 1:
            raise RuntimeError('controlled_attack_runtime_boundary_changed')
        native = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')
        return ('const OPTIONS = ' + json.dumps(options) + ';\n' +
                body.replace(marker, native + '\n' + marker, 1))


def pending_attack(config):
    path = config.data_dir / 'hunt-attacks.sqlite3'
    if not path.exists():
        return False
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT 1 FROM attacks WHERE world=? AND status IN "
                          "('prepared','submitted','accepted','unknown') LIMIT 1",
                          (config.identity['world'],)).fetchone() is not None


def _assert_external_clear(config, army_id):
    world = config.identity['world']
    path = config.data_dir / 'hunts.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path)) as db:
            row = db.execute("SELECT 1 FROM hunt_armies a JOIN hunts h ON a.hunt_id=h.id "
                             "WHERE h.world=? AND a.army_id=? AND h.status IN ('running','paused')",
                             (world, army_id)).fetchone()
            if row:
                raise RuntimeError('attack_army_assigned_to_hunt')
    path = config.data_dir / 'replenish' / 'commands.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path)) as db:
            row = db.execute("SELECT 1 FROM commands WHERE world=? AND army_id=? "
                             "AND status IN ('submitted','accepted','unknown')", (world, army_id)).fetchone()
            if row:
                raise RuntimeError('attack_replenishment_pending')


def execute_controlled(config, *, request_id, plan, runtime_factory=ControlledAttackRuntime):
    """Public bounded test entrypoint. Production hunting remains unavailable."""
    with closing(DataLease(config.data_dir)):
        return execute_under_lease(config, request_id=request_id, plan=plan, runtime_factory=runtime_factory)


def _departure_runtime_clean(state, generation, phase):
    return (isinstance(state, dict) and state.get('generation') == generation
            and state.get('phase') == phase and state.get('bridge_retained') is False
            and 'fault' in state and state['fault'] is None
            and 'cleanup_fault' in state and state['cleanup_fault'] is None)


def _read_departure_status(runtime, record, *, timeout):
    """Read one same-generation status snapshot with a temporary RPC budget."""
    absent = object()
    previous = getattr(runtime, 'request_timeout', absent)
    try:
        runtime.request_timeout = timeout
        raw = runtime.roster_status()
    finally:
        if previous is absent:
            del runtime.request_timeout
        else:
            runtime.request_timeout = previous
    if not isinstance(raw, dict) or raw.get('complete') is not True or not isinstance(raw.get('armies'), list):
        return raw, None
    matches = [row for row in raw['armies'] if isinstance(row, dict)
               and str(row.get('army_id')) == record['army_id']]
    if len(matches) == 1:
        try:
            validate_departure_witness(record, matches[0], now=time.time())
        except (ValueError, TypeError, KeyError):
            pass
        else:
            return raw, deepcopy(matches[0])
    return raw, None


def execute_under_lease(config, *, request_id, plan, runtime_factory=ControlledAttackRuntime,
                        stop_event=None):
    """Fixed send used by a caller already owning the service's data lease."""
    if plan.get('world') != config.identity['world']:
        raise ValueError('attack_world_mismatch')
    with AttackJournal(config.data_dir) as journal:
        if stop_event is not None and stop_event.is_set():
            previous = journal.db.execute('SELECT * FROM attacks WHERE request_id=?', (request_id,)).fetchone()
            if previous is not None:
                record = journal._decode(previous)
                if record['world'] != config.identity['world'] or _encode(record['plan']) != _encode(plan):
                    raise ValueError('attack_request_conflict')
                return record['result'] or {'status':'unknown','sent':None,'reason':'previous_send_claim:no_replay'}
            return {'status':'rejected','sent':False,'reason':'stopped_before_send_claim'}
        _assert_external_clear(config, plan['army_id'])
        record = journal.prepare(request_id, config.identity['world'], plan)
        if record['claimed_at'] is not None:
            return record['result'] or {'status':'unknown','sent':None,'reason':'previous_send_claim:no_replay'}
        if not journal.claim(record['id']):
            return {'status':'unknown','sent':None,'reason':'send_claim_lost:no_replay'}
        record = journal.get(record['id'])
        runtime, departure_row = None, None
        observation_started = None
        receipt = {'status':'unknown','sent':None,'reason':'native_outcome_unconfirmed'}
        try:
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError('stopped_after_send_claim:no_resend')
            runtime = runtime_factory(config, plan=plan, controlled_verification=True,
                                      **({'stop_event':stop_event} if stop_event is not None else {}))
            runtime.start()
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError('stopped_after_send_claim:no_resend')
            submitted = runtime.request('attack')
            if submitted.get('status') == 'rejected' and submitted.get('sent') is False:
                receipt = submitted
            elif submitted.get('status') == 'submitted' and submitted.get('sent') is True:
                observation_started = time.monotonic()
                deadline = observation_started + 15
                schedule = (.25,.75,1.5,3,5,10,15)
                next_poll = 0
                diagnostics = {'departure_poll_count':0, 'status_observed_after_claim_seconds':None}
                staged = {**record, 'status':'unknown', 'result':{'status':'unknown','sent':True}}
                events = []
                while True:
                    tick = time.monotonic()
                    if stop_event is not None and stop_event.is_set():
                        receipt['reason'] = 'stopped_after_send_claim:no_resend'
                        break
                    rows = runtime.events.events(generation=runtime.generation)
                    events = [{**r['event'], 'sequence':r['sequence']} for r in rows]
                    receipt = command_receipt(attack_request(plan), events)
                    if receipt['status'] in ('accepted','rejected'):
                        break
                    if not _departure_runtime_clean(runtime.state(), runtime.generation, 'running'):
                        break
                    tick = time.monotonic()
                    elapsed = tick-observation_started
                    if next_poll < len(schedule) and elapsed >= schedule[next_poll]:
                        # Skip missed slots rather than issuing a burst after a
                        # slow callback. One final healthy read retains the old
                        # fallback, bounded to a quarter-second RPC grace.
                        while next_poll+1 < len(schedule) and elapsed >= schedule[next_poll+1]:
                            next_poll += 1
                        budget = min(1,deadline-tick) if tick < deadline else .25
                        diagnostics['departure_poll_count'] += 1
                        try:
                            raw, candidate = _read_departure_status(runtime, staged, timeout=budget)
                            diagnostics['departure_status_read'] = deepcopy(raw)
                        except Exception as error:
                            diagnostics['departure_read_error'] = str(error)
                            break  # Never retry a native status RPC error.
                        if candidate is not None:
                            departure_row = candidate
                            diagnostics['status_observed_after_claim_seconds'] = candidate['observed_at']-record['claimed_at']
                            break
                        next_poll += 1
                    if tick >= deadline:
                        break
                    remaining = deadline-time.monotonic()
                    if remaining <= 0:
                        continue
                    until_poll = observation_started+schedule[next_poll]-time.monotonic() if next_poll < len(schedule) else remaining
                    time.sleep(max(0,min(.05,remaining,until_poll)))
                if receipt['status'] == 'unknown':
                    receipt['sent'] = submitted.get('sent')
                receipt['events'] = events
                acks = [e['epoch']-record['claimed_at'] for e in events
                        if e.get('name') == 'cs_army_cmd.response'
                        and type(e.get('epoch')) in (int,float) and math.isfinite(e['epoch'])
                        and e['epoch'] >= record['claimed_at']]
                receipt.update(diagnostics)
                receipt['untagged_ack_response_after_claim_seconds'] = min(acks) if acks else None
            else:
                receipt = {'status':'unknown','sent':submitted.get('sent'),
                           'reason':'native_send_unconfirmed','native_result':submitted}
            receipt['runtime_generation'] = runtime.generation
        except Exception as error:
            receipt = {'status':'unknown','sent':None,'reason':str(error)}
        finally:
            if runtime is not None:
                try:
                    runtime.close()
                    receipt['runtime_final'] = runtime.state()
                except Exception as error:
                    receipt = {**receipt,'status':'unknown','reason':'cleanup_unconfirmed:'+str(error),
                               'runtime_final':runtime.state()}
            receipt['command_id'] = record['id']
            if observation_started is not None:
                receipt['confirmation_elapsed_seconds'] = max(0,time.monotonic()-observation_started)
            journal.finish(record['id'], receipt)
        if (departure_row is not None and receipt['status'] == 'unknown' and receipt.get('sent') is True
                and _departure_runtime_clean(receipt.get('runtime_final'), runtime.generation, 'closed')):
            try:
                receipt = journal.reconcile_departure(record['id'], departure_row, now=time.time())
            except (ValueError, TypeError, KeyError):
                pass  # Keep the original unknown receipt and its durable fence.
        return receipt
