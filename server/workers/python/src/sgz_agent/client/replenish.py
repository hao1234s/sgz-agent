"""API-only, journaled replenishment. Does not dispatch or recruit armies."""
from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid

from .lease import DataLease
from .hunt_client import _positive_id


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def plan_replenishment(row):
    identity = str(row['army_id'])
    result = {'army_id': identity, 'status': 'blocked', 'sent': False}
    def blocked(reason):
        return {**result, 'reason': reason}
    if row.get('phase') != 'idle': return blocked('army_not_idle')
    if row.get('ordinary_verified') is not True: return blocked('ordinary_reserve_unverified')
    if not row.get('home_city_id') or row.get('current_base_id') != row['home_city_id']:
        return blocked('not_in_own_city')
    if row.get('recruitment_clear') is not True: return blocked('recruitment_state_unverified')
    heroes = row.get('heroes')
    if (not isinstance(heroes, list) or not 1 <= len(heroes) <= 3 or
        any(not isinstance(h, dict) or type(h.get('troops')) is not int or
            type(h.get('capacity')) is not int or not 0 <= h['troops'] <= h['capacity'] <= 100000
            or h['capacity'] == 0 for h in heroes)):
        return blocked('invalid_per_general_troops')
    try:
        ids = [_positive_id(h.get('id')) for h in heroes]
        army_id = _positive_id(identity)
    except ValueError: return blocked('invalid_army_or_hero_id')
    if len(set(ids)) != len(ids): return blocked('duplicate_hero')
    if any(h.get('recovering') is True for h in heroes): return blocked('wounded_recovery_pending')
    deficit = sum(h['capacity'] - h['troops'] for h in heroes)
    if not deficit: return {**result, 'status': 'already_full', 'troops': sum(h['troops'] for h in heroes)}
    if type(row.get('reserve')) is not int or row['reserve'] < deficit:
        return blocked('reserve_insufficient')
    return {**result, 'status': 'ready', 'home_city_id': row['home_city_id'],
            'heroes': heroes, 'deficit': deficit, 'reserve_before': row['reserve'],
            'request': {'army_id': army_id, 'hero_id_list': ids,
                        'hero_troop_list': [h['capacity'] for h in heroes], 'cons_index': 1}}


def verified_full(plan, result):
    if (result.get('army_id') != plan['army_id'] or result.get('army_ack') is not True or
        result.get('response_ack') is not True):
        return False
    actual = result.get('heroes')
    if not isinstance(actual, list) or len(actual) != len(plan['heroes']): return False
    expected = {str(h['id']): h['capacity'] for h in plan['heroes']}
    return (len({str(h.get('id')) for h in actual}) == len(expected) and
            all(str(h.get('id')) in expected and type(h.get('troops')) is int and
                h['troops'] == h.get('capacity') == expected[str(h['id'])] for h in actual))


def _old_targets(command):
    """Validate the narrowly supported absolute per-hero assignment contract."""
    try:
        payload = json.loads(command['payload'])
        request, heroes = payload['request'], payload['heroes']
        ids, amounts = request['hero_id_list'], request['hero_troop_list']
        if (payload['army_id'] != command['army_id'] or
            type(request['army_id']) is not int or str(request['army_id']) != command['army_id'] or
            type(request['cons_index']) is not int or request['cons_index'] != 1 or
            not isinstance(heroes,list) or not 1 <= len(heroes) <= 3 or
            not isinstance(ids,list) or not isinstance(amounts,list) or
            len(ids) != len(heroes) or len(amounts) != len(heroes)):
            return None
        targets = {}
        for hero, identity, amount in zip(heroes,ids,amounts):
            if (type(identity) is not int or _positive_id(str(identity)) != identity or
                type(amount) is not int or not 0 < amount <= 100000 or
                not isinstance(hero,dict) or str(hero.get('id')) != str(identity) or
                type(hero.get('capacity')) is not int or hero['capacity'] != amount or
                type(hero.get('troops')) is not int or not 0 <= hero['troops'] <= amount or
                str(identity) in targets):
                return None
            targets[str(identity)] = amount
        return payload, targets
    except (ValueError,KeyError,TypeError):
        return None


def _observation_row(snapshot, army_id, identity, now):
    if not isinstance(snapshot,dict) or snapshot.get('complete') is not True:
        return None, 'reconciliation_roster_incomplete'
    if snapshot.get('identity') != identity:
        return None, 'reconciliation_identity_mismatch'
    observed = snapshot.get('observed_at')
    if (type(observed) not in (int,float) or not math.isfinite(observed) or
        not now - 30 <= observed <= now + 1):
        return None, 'reconciliation_observation_not_fresh'
    lifecycle = snapshot.get('native_lifecycle')
    if (snapshot.get('cleanup_verified') is not True or not isinstance(lifecycle,dict) or
        lifecycle.get('closed') is not True or
        any(type(lifecycle.get(k)) is not int or lifecycle[k] != 0
            for k in ('active_native','callbacks','hooks'))):
        return None, 'reconciliation_cleanup_unverified'
    rows = snapshot.get('armies')
    if not isinstance(rows,list) or not 1 <= len(rows) <= 50 or any(not isinstance(r,dict) for r in rows):
        return None, 'reconciliation_invalid_roster'
    ids = [r.get('army_id') for r in rows]
    if any(not isinstance(i,str) for i in ids) or len(set(ids)) != len(ids) or army_id not in ids:
        return None, 'reconciliation_army_missing_or_duplicated'
    row = rows[ids.index(army_id)]
    plan = plan_replenishment(row)
    # A reserve shortage does not invalidate observation of already met old targets.
    if plan['status'] not in {'ready','already_full'} and plan.get('reason') != 'reserve_insufficient':
        return None, plan.get('reason','reconciliation_army_unready')
    if any(h.get('recovering') is not False for h in row['heroes']):
        return None, 'wounded_recovery_unverified'
    return row, None


def _old_goal_satisfied(command, row):
    parsed = _old_targets(command)
    if not parsed:
        return False
    payload, targets = parsed
    actual = {str(h['id']):h for h in row['heroes']}
    return (row['home_city_id'] == payload.get('home_city_id') and
            all(identity in actual and actual[identity]['troops'] == actual[identity]['capacity'] == amount
                for identity,amount in targets.items()))


def _resolution_scope(command, identity):
    return {'kind':'allow_compatible_extension','contract_version':1,
            'command_id':command['id'],'army_id':command['army_id'],'world':command['world'],
            'identity':identity,'payload_sha256':sha256(command['payload'].encode('utf-8')).hexdigest()}


def _plan_contract(plan):
    return {'army_id':plan['army_id'],'home_city_id':plan['home_city_id'],'request':plan['request'],
            'heroes':[{'id':str(h['id']),'troops':h['troops'],'capacity':h['capacity']} for h in plan['heroes']]}


@dataclass(frozen=True)
class HuntReplenishOwnership:
    hunt_id: str
    command_id: str
    world: str
    army_id: str
    payload_sha256: str


class HuntReplenishmentBlocked(RuntimeError):
    """An internal hunt ownership/plan refusal before any replenishment send."""
    sent = False


def _request_scope(world, selection, ownership=None):
    scope = {'world':world,'selection':selection}
    if ownership:
        scope['hunt'] = {'hunt_id':ownership.hunt_id,'command_id':ownership.command_id,
                         'payload_sha256':ownership.payload_sha256}
    return encode(scope)


class Replenisher:
    def __init__(self, config, *, client=None):
        self.config = config
        if client is None:
            from .replenish_client import ReplenishClient
            client = ReplenishClient(config)
        self.client = client

    @contextmanager
    def _journal(self):
        directory = self.config.data_dir / 'replenish'
        lease = DataLease(directory)
        try:
            with closing(sqlite3.connect(directory / 'commands.sqlite3')) as db:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('PRAGMA synchronous=FULL')
                db.executescript('''CREATE TABLE IF NOT EXISTS requests(
                    id TEXT PRIMARY KEY,scope TEXT NOT NULL,result TEXT);
                    CREATE TABLE IF NOT EXISTS commands(
                    id TEXT PRIMARY KEY,request_id TEXT NOT NULL,world TEXT NOT NULL,
                    army_id TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,result TEXT,
                    extension_resolution_id TEXT);
                    CREATE TABLE IF NOT EXISTS resolutions(
                    id TEXT PRIMARY KEY,command_id TEXT NOT NULL,kind TEXT NOT NULL,evidence TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS confirmed_successors(
                    id TEXT PRIMARY KEY,command_id TEXT NOT NULL UNIQUE,
                    successor_command_id TEXT NOT NULL,evidence TEXT NOT NULL);''')
                if 'extension_resolution_id' not in {r['name'] for r in db.execute('PRAGMA table_info(commands)')}:
                    db.execute('ALTER TABLE commands ADD COLUMN extension_resolution_id TEXT')
                old_index = db.execute("SELECT sql FROM sqlite_master WHERE name='one_pending_per_army'").fetchone()
                if old_index and 'extension_resolution_id' not in old_index['sql']:
                    db.execute('DROP INDEX one_pending_per_army')
                # Resolved commands remain unknown. Every run still checks every such
                # command; exclusion here permits only a separately checked extension.
                db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS one_pending_per_army ON commands(world,army_id)
                    WHERE status IN ('submitted','unknown','accepted') AND extension_resolution_id IS NULL''')
                db.commit()
                yield db
        finally:
            lease.close()

    def reconcile(self, *, world, command_id):
        """Observe old absolute goals twice; never send or infer historical success."""
        if world != self.config.identity.get('world'): raise ValueError('world_mismatch')
        if not isinstance(command_id,str) or not command_id: raise ValueError('command_id_required')
        with self._journal() as db:
            command = db.execute('SELECT * FROM commands WHERE id=? AND world=?',(command_id,world)).fetchone()
            if not command: raise ValueError('command_not_found')
            result = {'command_id':command_id,'army_id':command['army_id'],'status':'blocked','sent':False}
            if command['status'] != 'unknown' or not _old_targets(command):
                return {**result,'reason':'reconciliation_command_not_supported'}
            self._hunt_conflict([command['army_id']])
            observations = []
            for _ in range(2):
                snapshot = self.client.snapshot(army_ids=[command['army_id']])
                row, reason = _observation_row(snapshot,command['army_id'],self.config.identity,time.time())
                if reason: return {**result,'reason':reason}
                if observations and snapshot['observed_at'] <= observations[-1]['observed_at']:
                    return {**result,'reason':'reconciliation_observation_not_increasing'}
                if not _old_goal_satisfied(command,row):
                    return {**result,'reason':'reconciliation_old_targets_not_satisfied'}
                observations.append(snapshot)
            recorded_at = time.time()
            for observation in observations:
                _, reason = _observation_row(observation,command['army_id'],self.config.identity,recorded_at)
                if reason: return {**result,'reason':reason}
            scope = _resolution_scope(command,self.config.identity)
            evidence = {'scope':scope,'scope_sha256':sha256(encode(scope).encode('utf-8')).hexdigest(),
                        'observations':observations,'recorded_at':recorded_at,
                        'historical_outcome':'unknown','transport_quiescence_claimed':False}
            resolution_id = uuid.uuid4().hex
            with db:
                db.execute('INSERT INTO resolutions VALUES(?,?,?,?)',
                           (resolution_id,command_id,'allow_compatible_extension',encode(evidence)))
                db.execute('UPDATE commands SET extension_resolution_id=? WHERE id=?',(resolution_id,command_id))
            return {**result,'status':'allow_compatible_extension','resolution_id':resolution_id}

    def _extension_evidence(self, db, command):
        if command['status'] != 'unknown' or not command['extension_resolution_id']: return None
        resolution = db.execute('SELECT * FROM resolutions WHERE id=? AND command_id=?',
                               (command['extension_resolution_id'],command['id'])).fetchone()
        if not resolution or resolution['kind'] != 'allow_compatible_extension': return None
        try:
            evidence = json.loads(resolution['evidence'])
            scope = _resolution_scope(command,self.config.identity)
            if (evidence['scope'] != scope or evidence['scope_sha256'] != sha256(encode(scope).encode('utf-8')).hexdigest() or
                evidence.get('historical_outcome') != 'unknown' or evidence.get('transport_quiescence_claimed') is not False):
                return None
            observations, recorded = evidence['observations'], evidence['recorded_at']
            if (type(recorded) not in (int,float) or not math.isfinite(recorded) or recorded > time.time()+1 or
                not isinstance(observations,list) or len(observations) != 2): return None
            for observation in observations:
                row, reason = _observation_row(observation,command['army_id'],self.config.identity,recorded)
                if reason or not _old_goal_satisfied(command,row): return None
            if observations[0]['observed_at'] >= observations[1]['observed_at']: return None
            return evidence
        except (KeyError,ValueError,TypeError):
            return None

    def _successor_proof(self, db, command, successor):
        evidence = self._extension_evidence(db,command)
        old = _old_targets(command)
        new = _old_targets(successor)
        if (not evidence or not old or not new or successor['id'] == command['id'] or
            successor['request_id'] == command['request_id'] or
            successor['status'] != 'applied' or successor['world'] != command['world'] or
            successor['army_id'] != command['army_id']): return None
        try:
            payload, targets = new
            old_payload, old_targets = old
            if (payload['home_city_id'] != old_payload['home_city_id'] or len(targets) <= len(old_targets) or
                any(targets.get(identity) != amount for identity,amount in old_targets.items())): return None
            observation = evidence['observations'][-1]
            row, reason = _observation_row(observation,command['army_id'],self.config.identity,evidence['recorded_at'])
            observed_plan = plan_replenishment(row) if not reason else None
            if (not observed_plan or observed_plan['status'] != 'ready' or
                _plan_contract(payload) != _plan_contract(observed_plan)): return None
            order = {r['id']:r['rowid'] for r in db.execute('SELECT rowid,id FROM commands WHERE id IN (?,?)',
                                                           (command['id'],successor['id']))}
            if order[successor['id']] <= order[command['id']]: return None
            result = json.loads(successor['result'])
            lifecycle = result.get('native_lifecycle')
            if (result.get('status') != 'applied' or result.get('sent') is not True or
                result.get('command_id') != successor['id'] or not verified_full(payload,result) or
                type(result.get('native_calls')) is not int or result['native_calls'] != 1 or
                type(result.get('encoded')) is not int or not 0 <= result['encoded'] <= 1 or
                result.get('ambiguous') is not False or result.get('cleanup_verified') is not True or
                not isinstance(lifecycle,dict) or lifecycle.get('closed') is not True or
                any(type(lifecycle.get(k)) is not int or lifecycle[k] != 0
                    for k in ('active_native','callbacks','hooks'))): return None
            resolution = db.execute('SELECT evidence FROM resolutions WHERE id=?',
                                    (command['extension_resolution_id'],)).fetchone()
            return {'kind':'confirmed_successor','contract_version':1,'command_id':command['id'],
                    'successor_command_id':successor['id'],'resolution_id':command['extension_resolution_id'],
                    'resolution_sha256':sha256(resolution['evidence'].encode('utf-8')).hexdigest(),
                    'predecessor_payload_sha256':sha256(command['payload'].encode('utf-8')).hexdigest(),
                    'successor_payload_sha256':sha256(successor['payload'].encode('utf-8')).hexdigest(),
                    'successor_result_sha256':sha256(successor['result'].encode('utf-8')).hexdigest(),
                    'identity':dict(self.config.identity),'world':command['world'],'army_id':command['army_id'],
                    'home_city_id':payload['home_city_id'],'cons_index':1,'historical_outcome':'unknown'}
        except (KeyError,ValueError,TypeError):
            return None

    def _record_successor(self, db, command, successor):
        proof = self._successor_proof(db,command,successor)
        if not proof: return None
        existing = db.execute('SELECT * FROM confirmed_successors WHERE command_id=?',(command['id'],)).fetchone()
        if existing:
            return existing['id'] if existing['evidence'] == encode(proof) else None
        audit_id = uuid.uuid4().hex
        db.execute('INSERT INTO confirmed_successors VALUES(?,?,?,?)',
                   (audit_id,command['id'],successor['id'],encode(proof)))
        return audit_id

    def confirm_successor(self, *, world, command_id, successor_command_id):
        """Explicitly adopt verified historical execution; do not execute cached requests."""
        if world != self.config.identity.get('world'): raise ValueError('world_mismatch')
        if any(not isinstance(v,str) or not v for v in (command_id,successor_command_id)):
            raise ValueError('command_id_required')
        result = {'command_id':command_id,'successor_command_id':successor_command_id,'status':'blocked','sent':False}
        with self._journal() as db:
            command = db.execute('SELECT * FROM commands WHERE id=? AND world=?',(command_id,world)).fetchone()
            successor = db.execute('SELECT * FROM commands WHERE id=? AND world=?',(successor_command_id,world)).fetchone()
            if not command or not successor: return {**result,'reason':'successor_proof_not_verified'}
            with db:
                audit_id = self._record_successor(db,command,successor)
            return ({**result,'status':'confirmed_successor','audit_id':audit_id} if audit_id else
                    {**result,'reason':'successor_proof_not_verified'})

    def _retired_fence_applies(self, db, command, snapshot):
        audit = db.execute('SELECT * FROM confirmed_successors WHERE command_id=?',(command['id'],)).fetchone()
        if not audit: return False
        successor = db.execute('SELECT * FROM commands WHERE id=?',(audit['successor_command_id'],)).fetchone()
        if not successor: return False
        proof = self._successor_proof(db,command,successor)
        if not proof or audit['evidence'] != encode(proof): return False
        row, reason = _observation_row(snapshot,command['army_id'],self.config.identity,time.time())
        if reason or row['home_city_id'] != proof['home_city_id']: return False
        # Retirement authorizes later losses, while changed membership/capacity
        # still require a separate causal proof rather than reusing old targets.
        _, targets = _old_targets(command)
        actual = {str(h['id']):h for h in row['heroes']}
        return all(identity in actual and actual[identity]['capacity'] == amount for identity,amount in targets.items())

    def _allows_extension(self, db, command, plan, snapshot):
        if self._retired_fence_applies(db,command,snapshot): return True
        if plan['status'] not in {'ready','already_full'}: return False
        evidence = self._extension_evidence(db,command)
        if not evidence: return False
        try:
            observations = evidence['observations']
            row, reason = _observation_row(snapshot,command['army_id'],self.config.identity,time.time())
            if reason or snapshot['observed_at'] <= observations[1]['observed_at'] or not _old_goal_satisfied(command,row):
                return False
            # This is a verified no-op, with the historical unknown still visible.
            if plan['status'] == 'already_full': return True
            _, targets = _old_targets(command)
            new_targets = dict(zip(map(str,plan['request']['hero_id_list']),plan['request']['hero_troop_list']))
            return (len(new_targets) > len(targets) and
                    all(new_targets.get(identity) == amount for identity,amount in targets.items()) and
                    any(str(h['id']) not in targets and h['troops'] < h['capacity'] for h in plan['heroes']))
        except (KeyError,ValueError,TypeError):
            return False

    def _read_hunt_ownership(self, db, hunt_id, command_id, *, active):
        record = db.execute('''SELECT h.world,h.status AS hunt_status,c.army_id,c.kind,c.status AS command_status,
            c.payload,a.state FROM hunt_commands c JOIN hunts h ON h.id=c.hunt_id
            JOIN hunt_armies a ON a.hunt_id=h.id AND a.army_id=c.army_id
            WHERE h.id=? AND c.id=?''',(hunt_id,command_id)).fetchone()
        try:
            if not record or record['world'] != self.config.identity['world'] or record['kind'] != 'replenish':
                raise ValueError('ownership')
            _positive_id(record['army_id'])
            payload,state = json.loads(record['payload']),json.loads(record['state'])
            if not isinstance(payload,dict) or not isinstance(state,dict): raise ValueError('ownership')
            if active and (record['hunt_status'] != 'running' or record['command_status'] != 'submitted' or
                           state.get('army_id') != record['army_id'] or state.get('phase') not in {'idle','stationed','resolved'} or
                           state.get('pending_command_id') != command_id or state.get('encounter_open') is not False or
                           state.get('blocked') is True):
                raise ValueError('ownership')
            return HuntReplenishOwnership(hunt_id,command_id,record['world'],record['army_id'],
                                          sha256(record['payload'].encode('utf-8')).hexdigest()),payload
        except (ValueError,TypeError,KeyError):
            raise HuntReplenishmentBlocked('hunt_replenish_ownership_unverified') from None

    def _hunt_context(self, hunt_id, command_id):
        if any(not isinstance(v,str) or not v for v in (hunt_id,command_id)):
            raise ValueError('hunt_command_id_required')
        path = self.config.data_dir / 'hunts.sqlite3'
        if not path.exists(): raise HuntReplenishmentBlocked('hunt_replenish_ownership_unverified')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro',uri=True)) as hunt_db:
            hunt_db.row_factory = sqlite3.Row
            # Cached results remain readable after the coordinator finishes or stops.
            ownership,_ = self._read_hunt_ownership(hunt_db,hunt_id,command_id,active=False)
        request_id = 'hunt-replenish:' + sha256(encode({'hunt_id':hunt_id,'command_id':command_id}).encode('utf-8')).hexdigest()
        return ownership,request_id

    def run_for_hunt(self, *, hunt_id, command_id):
        """Internal adapter entry: derive ownership and idempotency from the hunt journal."""
        ownership,request_id = self._hunt_context(hunt_id,command_id)
        with self._journal() as db:
            return self._run(db,ownership.world,request_id,[ownership.army_id],[ownership.army_id],False,
                             ownership=ownership)

    def cached_for_hunt(self, *, hunt_id, command_id):
        """Read persisted evidence only; absence never authorizes a new execution."""
        ownership,request_id = self._hunt_context(hunt_id,command_id)
        path = self.config.data_dir / 'replenish' / 'commands.sqlite3'
        if not path.exists(): return None
        scope = _request_scope(ownership.world,[ownership.army_id],ownership)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro',uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            request = db.execute('SELECT scope,result FROM requests WHERE id=?',(request_id,)).fetchone()
            if not request: return None
            if request['scope'] != scope: raise ValueError('request_conflict')
            children = db.execute('SELECT * FROM commands WHERE request_id=?',(request_id,)).fetchall()
            if len(children) > 1: return None
            child = children[0] if children else None
            if child and (child['world'] != ownership.world or child['army_id'] != ownership.army_id): return None
            link = {'hunt_id':hunt_id,'hunt_command_id':command_id}
            try:
                child_result = json.loads(child['result']) if child and child['result'] else None
                applied = (child is not None and child['status'] == 'applied' and
                           isinstance(child_result,dict) and child_result.get('status') == 'applied' and
                           child_result.get('sent') is True and child_result.get('command_id') == child['id'] and
                           _old_targets(child) is not None and verified_full(json.loads(child['payload']),child_result))
                if request['result']:
                    outcome = json.loads(request['result'])
                    armies = outcome.get('armies') if isinstance(outcome,dict) else None
                    if (outcome.get('request_id') != request_id or outcome.get('world') != ownership.world or
                        not isinstance(armies,list) or len(armies) != 1 or not isinstance(armies[0],dict) or
                        armies[0].get('army_id') != ownership.army_id): return None
                    result = armies[0]
                    if result.get('status') == 'applied':
                        if not applied or result != child_result: return None
                    elif result.get('status') in {'already_full','blocked','rejected'} and result.get('sent') is False:
                        if child and (child['status'] != result['status'] or child_result != result): return None
                    else:
                        return None
                    return {**outcome,**link}
                if not applied: return None
                return {'request_id':request_id,'world':ownership.world,'dry_run':False,'status':'complete',
                        'armies':[child_result],'source':'persisted_command',**link}
            except (ValueError,KeyError,TypeError,AttributeError):
                return None

    def _hunt_payload_matches(self, ownership, payload, snapshot):
        row,reason = _observation_row(snapshot,ownership.army_id,self.config.identity,time.time())
        if reason: return False
        try:
            heroes = payload['heroes']
            if (payload.get('base_id') != row['home_city_id'] or payload.get('expected_phase') != row['phase'] or
                type(payload.get('desired_total')) is not int or
                payload['desired_total'] != sum(h['capacity'] for h in row['heroes']) or
                not isinstance(heroes,list) or len(heroes) != len(row['heroes'])): return False
            expected = {}
            for hero in heroes:
                if (not isinstance(hero,dict) or type(hero.get('troops')) is not int or
                    type(hero.get('capacity')) is not int or not 0 <= hero['troops'] <= hero['capacity']): return False
                identity = str(_positive_id(hero.get('id')))
                if identity in expected: return False
                expected[identity] = (hero['troops'],hero['capacity'])
            actual = {str(h['id']):(h['troops'],h['capacity']) for h in row['heroes']}
            return expected == actual
        except (KeyError,ValueError,TypeError):
            return False

    def _hunt_conflict(self, selection, *, ownership=None):
        path = self.config.data_dir / 'hunts.sqlite3'
        if not path.exists():
            if ownership: raise HuntReplenishmentBlocked('hunt_replenish_ownership_unverified')
            return
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            payload = None
            if ownership:
                current,payload = self._read_hunt_ownership(db,ownership.hunt_id,ownership.command_id,active=True)
                if current != ownership or selection != [ownership.army_id]:
                    raise HuntReplenishmentBlocked('hunt_replenish_ownership_unverified')
            rows = db.execute('''SELECT h.id,h.status,a.army_id,a.state FROM hunt_armies a
                JOIN hunts h ON h.id=a.hunt_id WHERE h.world=?''',
                (self.config.identity['world'],)).fetchall()
            pending = db.execute('''SELECT c.id,c.hunt_id,c.army_id FROM hunt_commands c JOIN hunts h ON h.id=c.hunt_id
                WHERE h.world=? AND c.status IN ('submitted','accepted','unknown')''',
                (self.config.identity['world'],)).fetchall()
        for command in pending:
            army_id = command['army_id']
            if ownership and (command['id'],command['hunt_id'],army_id) == (ownership.command_id,ownership.hunt_id,ownership.army_id):
                continue
            if selection == 'all' or army_id in selection:
                error = HuntReplenishmentBlocked if ownership else RuntimeError
                raise error('hunt_owns_army:' + army_id)
        for hunt_id,status,army_id,state in rows:
            if selection != 'all' and army_id not in selection: continue
            if ownership and (hunt_id,army_id) == (ownership.hunt_id,ownership.army_id): continue
            data = json.loads(state)
            if status == 'running' or data.get('encounter_open') or data.get('pending_command_id'):
                error = HuntReplenishmentBlocked if ownership else RuntimeError
                raise error('hunt_owns_army:' + army_id)
        return payload

    def run(self, *, world, request_id, army_ids=None, dry_run=False):
        if world != self.config.identity.get('world'): raise ValueError('world_mismatch')
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
            raise ValueError('request_id_required')
        if type(dry_run) is not bool: raise ValueError('dry_run_must_be_boolean')
        if army_ids is not None:
            if (not isinstance(army_ids, list) or not 1 <= len(army_ids) <= 50 or
                any(not isinstance(a, str) for a in army_ids) or len(set(army_ids)) != len(army_ids)):
                raise ValueError('army_ids_invalid')
            for identity in army_ids: _positive_id(identity)
        selection = sorted(army_ids) if army_ids is not None else 'all'
        with self._journal() as db:
            return self._run(db, world, request_id, selection, army_ids, dry_run)

    def _run(self, db, world, request_id, selection, army_ids, dry_run, *, ownership=None):
        scope = _request_scope(world,selection,ownership)
        previous = db.execute('SELECT scope,result FROM requests WHERE id=?', (request_id,)).fetchone()
        if previous:
            if previous[0] != scope: raise ValueError('request_conflict')
            if previous[1]: return json.loads(previous[1])
            commands = db.execute('SELECT army_id,status,result FROM commands WHERE request_id=?', (request_id,)).fetchall()
            return {'request_id': request_id, 'world': world, 'status': 'interrupted',
                    'armies': [{'army_id': a, 'status': 'unknown' if s == 'submitted' else s,
                                'reason': 'interrupted_request_no_replay'} for a,s,_ in commands]}
        if not dry_run and hasattr(self.client,'ensure_execution_ready'):
            self.client.ensure_execution_ready()
        self._hunt_conflict(selection,ownership=ownership)
        snapshot = self.client.snapshot(army_ids=army_ids)
        if snapshot.get('complete') is not True: raise RuntimeError('roster_incomplete')
        rows = snapshot.get('armies', [])
        if not isinstance(rows, list) or len(rows) > 50: raise RuntimeError('invalid_roster')
        identities = [r.get('army_id') for r in rows]
        if any(not isinstance(a,str) for a in identities) or len(set(identities)) != len(identities):
            raise RuntimeError('invalid_roster_ids')
        if army_ids is not None and any(a not in identities for a in army_ids):
            raise ValueError('army_not_owned_or_missing')
        rows = [r for r in rows if army_ids is None or r['army_id'] in army_ids]
        if not rows: raise ValueError('no_eligible_armies')
        if ownership:
            payload = self._hunt_conflict(selection,ownership=ownership)
            if len(rows) != 1 or not self._hunt_payload_matches(ownership,payload,snapshot):
                raise HuntReplenishmentBlocked('hunt_replenish_payload_mismatch')
        if not dry_run:
            db.execute('INSERT INTO requests VALUES(?,?,NULL)', (request_id,scope)); db.commit()
        results, failed = [], False
        for row in rows:
            plan = plan_replenishment(row)
            if failed:
                results.append({'army_id':row['army_id'],'status':'blocked','sent':False,'reason':'previous_native_operation_failed'})
                continue
            pending = db.execute("SELECT * FROM commands WHERE world=? AND army_id=? AND status IN ('submitted','unknown','accepted')",
                                 (world,row['army_id'])).fetchall()
            blocking = next((c for c in pending if not self._allows_extension(db,c,plan,snapshot)),None)
            if blocking:
                results.append({'army_id':row['army_id'],'status':'blocked','sent':False,
                                'reason':'previous_command_unknown','command_id':blocking['id']})
                continue
            if plan['status'] != 'ready' or dry_run:
                if pending and plan['status'] == 'already_full':
                    plan = {**plan,'historical_unknown_command_ids':[c['id'] for c in pending]}
                results.append(plan); continue
            command_id = uuid.uuid4().hex
            if ownership:
                payload = self._hunt_conflict(selection,ownership=ownership)
                if not self._hunt_payload_matches(ownership,payload,snapshot):
                    raise HuntReplenishmentBlocked('hunt_replenish_payload_mismatch')
            db.execute('INSERT INTO commands(id,request_id,world,army_id,status,payload,result) VALUES(?,?,?,?,?,?,NULL)',
                       (command_id,request_id,world,row['army_id'],'submitted',encode(plan))); db.commit()
            try:
                response = self.client.execute(plan)
                if response.get('status') == 'applied' and verified_full(plan,response):
                    result = {**response, 'status':'applied'}
                elif response.get('sent') is False and response.get('status') in {'blocked','rejected','already_full'}:
                    result = response
                else:
                    result = {**response,'status':'unknown','reason':response.get('reason') or 'full_troops_not_verified'}
                    failed = True
            except Exception as error:
                result = {'status':'unknown','sent':None,'reason':str(error)}
                failed = True
            result = {**result, 'army_id':row['army_id'], 'command_id':command_id}
            with db:
                db.execute('UPDATE commands SET status=?,result=? WHERE id=?', (result['status'],encode(result),command_id))
                if result['status'] == 'applied':
                    successor = db.execute('SELECT * FROM commands WHERE id=?',(command_id,)).fetchone()
                    for previous_command in pending:
                        self._record_successor(db,previous_command,successor)
            results.append(result)
        outcome = {'request_id':request_id,'world':world,'dry_run':dry_run,'armies':results,
                   'status':'complete' if all(r['status'] in {'applied','already_full'} for r in results) else 'needs_attention'}
        if not dry_run:
            db.execute('UPDATE requests SET result=? WHERE id=?', (encode(outcome),request_id)); db.commit()
        return outcome
