"""Durable hunt campaigns, target reservations and a write-ahead command journal."""
import json
from pathlib import Path
import sqlite3
import threading
import uuid


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class HuntStore:
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(Path(directory) / 'hunts.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS hunts(
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, world TEXT NOT NULL,
                status TEXT NOT NULL, policy TEXT NOT NULL, selection TEXT NOT NULL,
                next_poll_at REAL NOT NULL, created_at REAL NOT NULL, reason TEXT);
            CREATE TABLE IF NOT EXISTS hunt_armies(
                hunt_id TEXT NOT NULL, army_id TEXT NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY(hunt_id,army_id));
            CREATE TABLE IF NOT EXISTS hunt_target_claims(
                world TEXT NOT NULL,target_id TEXT NOT NULL,hunt_id TEXT NOT NULL,army_id TEXT NOT NULL,
                PRIMARY KEY(world,target_id));
            CREATE TABLE IF NOT EXISTS hunt_commands(
                id TEXT PRIMARY KEY,hunt_id TEXT NOT NULL,army_id TEXT NOT NULL,
                kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,
                created_at REAL NOT NULL,result TEXT);
            CREATE TABLE IF NOT EXISTS hunt_events(
                id INTEGER PRIMARY KEY,hunt_id TEXT NOT NULL,army_id TEXT,
                at REAL NOT NULL,event TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS hunt_one_outstanding_command
                ON hunt_commands(hunt_id,army_id) WHERE status IN ('submitted','accepted','unknown');
        ''')

    def find_request(self, request_id):
        with self.lock:
            row = self.db.execute('SELECT id FROM hunts WHERE request_id=?',(request_id,)).fetchone()
            return self.get(row['id']) if row else None

    def create(self, request_id, world, selection, army_ids, policy, now, initial_statuses=None):
        with self.lock, self.db:
            previous = self.find_request(request_id)
            if previous:
                if (previous['world'], previous['selection'], previous['policy']) != (world, selection, policy):
                    raise ValueError('request_conflict')
                return previous
            if self.db.execute("SELECT 1 FROM hunts WHERE status='running'").fetchone():
                raise RuntimeError('hunt_already_running')
            # Paused campaigns can still have armies in flight; their reservations remain owned.
            for army_id in army_ids:
                if self.db.execute('''SELECT 1 FROM hunt_armies a JOIN hunts h ON a.hunt_id=h.id
                    WHERE a.army_id=? AND h.status IN ('running','paused')''',(army_id,)).fetchone():
                    raise RuntimeError('army_already_assigned:resume_existing_hunt')
            identity = uuid.uuid4().hex
            self.db.execute('INSERT INTO hunts VALUES(?,?,?,?,?,?,?,?,?)',
                (identity,request_id,world,'running',encode(policy),encode(selection),now,now,None))
            for army_id in army_ids:
                state = dict(army_id=army_id,phase='unknown',encounter_open=False,
                             target=None,return_base_id=None,pending_command_id=None,
                             refill_required=False,completed=0,reason=None,blocked=False)
                observed = (initial_statuses or {}).get(army_id,{})
                state['phase'] = observed.get('phase','unknown')
                if state['phase'] in {'fighting','draw'}:
                    state['encounter_open'] = state['adopted_encounter'] = True
                    if observed.get('target_id'):
                        state['target'] = {'id':observed['target_id']}
                        self.db.execute('INSERT OR IGNORE INTO hunt_target_claims VALUES(?,?,?,?)',
                                        (world,observed['target_id'],identity,army_id))
                self.db.execute('INSERT INTO hunt_armies VALUES(?,?,?)',(identity,army_id,encode(state)))
            return self.get(identity)

    def get(self, identity):
        with self.lock:
            row = self.db.execute('SELECT * FROM hunts WHERE id=?',(identity,)).fetchone()
            if row is None: raise ValueError('hunt_not_found')
            result = dict(row)
            for key in ('policy','selection'): result[key] = json.loads(result[key])
            result['armies'] = [json.loads(r['state']) for r in self.db.execute(
                'SELECT state FROM hunt_armies WHERE hunt_id=? ORDER BY army_id',(identity,))]
            result['pending_commands'] = [self.command(r['id']) for r in self.db.execute(
                "SELECT id FROM hunt_commands WHERE hunt_id=? AND status IN ('submitted','accepted','unknown')",(identity,))]
            return result

    def list(self):
        with self.lock:
            return [self.get(r['id']) for r in self.db.execute('SELECT id FROM hunts ORDER BY created_at DESC')]

    def update(self, identity, **changes):
        if not changes or not set(changes) <= {'status','next_poll_at','reason'}:
            raise ValueError('invalid_hunt_update')
        with self.lock, self.db:
            self.db.execute('UPDATE hunts SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',
                            (*changes.values(),identity))

    def save_army(self, identity, state):
        with self.lock, self.db:
            self.db.execute('UPDATE hunt_armies SET state=? WHERE hunt_id=? AND army_id=?',
                            (encode(state),identity,state['army_id']))

    def reserve(self, identity, target_id, army_id):
        with self.lock, self.db:
            world = self.get(identity)['world']
            row = self.db.execute('SELECT hunt_id,army_id FROM hunt_target_claims WHERE world=? AND target_id=?',
                                  (world,target_id)).fetchone()
            if row: return row['hunt_id'] == identity and row['army_id'] == army_id
            self.db.execute('INSERT INTO hunt_target_claims VALUES(?,?,?,?)',(world,target_id,identity,army_id))
            return True

    def release(self, identity, army_id):
        with self.lock, self.db:
            self.db.execute('DELETE FROM hunt_target_claims WHERE hunt_id=? AND army_id=?',(identity,army_id))

    def outstanding(self, identity, army_id):
        with self.lock:
            row = self.db.execute("SELECT id FROM hunt_commands WHERE hunt_id=? AND army_id=? AND status IN ('submitted','accepted','unknown')",
                                  (identity,army_id)).fetchone()
            return self.command(row['id']) if row else None

    def prepare_command(self, identity, state, kind, payload, now):
        with self.lock, self.db:
            if state.get('pending_command_id') or self.outstanding(identity,state['army_id']):
                raise RuntimeError('command_pending')
            if self.get(identity)['status'] != 'running': return None
            command_id = uuid.uuid4().hex
            if kind == 'attack': state['encounter_open'] = True
            state['pending_command_id'] = command_id
            # Durable before calling the client: even a crash before send requires reconciliation.
            self.db.execute('INSERT INTO hunt_commands VALUES(?,?,?,?,?,?,?,NULL)',
                (command_id,identity,state['army_id'],kind,encode(payload),'submitted',now))
            self.db.execute('UPDATE hunt_armies SET state=? WHERE hunt_id=? AND army_id=?',
                (encode(state),identity,state['army_id']))
            return self.command(command_id)

    def command(self, identity):
        with self.lock:
            row = self.db.execute('SELECT * FROM hunt_commands WHERE id=?',(identity,)).fetchone()
            if row is None: raise ValueError('command_not_found')
            result = dict(row)
            result['payload'] = json.loads(result['payload'])
            result['result'] = json.loads(result['result']) if result['result'] else None
            result['request_id'] = result['id']
            return result

    def finish_command(self, identity, status, result=None):
        if status not in {'accepted','rejected','unknown','applied','not_applied'}:
            raise ValueError('invalid_command_status')
        with self.lock, self.db:
            self.db.execute('UPDATE hunt_commands SET status=?,result=? WHERE id=?',
                (status,encode(result),identity))

    def event(self, identity, army_id, now, value):
        with self.lock, self.db:
            self.db.execute('INSERT INTO hunt_events(hunt_id,army_id,at,event) VALUES(?,?,?,?)',
                            (identity,army_id,now,encode(value)))

    def close(self):
        with self.lock: self.db.close()
