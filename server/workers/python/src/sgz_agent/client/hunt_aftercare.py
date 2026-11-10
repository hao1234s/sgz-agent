"""Durable, single-round return and troop-threshold aftercare.

This coordinator has no thread, attack, withdrawal or queue-cancellation API.
The service owns the native activity gate and cancellation. ``ready_for_next_attack``
is only a replenishment decision: a later attack still needs full dispatch guards.
Original attack receipts, dispatch snapshots and target fences are read-only here.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

from .hunt_reconcile import _identity, _number
from .hunt_retarget import _inactive_roster


POLL_STATES={'waiting_return','confirming_return','waiting_recruitment','waiting_recovery',
             'waiting_readiness','blocked_ready','blocked_capability'}
DONE_STATES={'complete','ready_for_next_attack'}
MIN_TROOPS=20000


def _encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def _hash(value):return hashlib.sha256(_encode(value).encode()).hexdigest()


def _ids(heroes):
    if not isinstance(heroes,list) or len(heroes)!=3 or any(not isinstance(h,dict) for h in heroes):
        raise ValueError('aftercare_formation_invalid')
    ids=[h.get('id') for h in heroes]
    if (any(isinstance(i,bool) or not str(i).isascii() or not str(i).isdigit() or int(i)<=0 for i in ids)
            or len({int(i) for i in ids})!=3):
        raise ValueError('aftercare_formation_invalid')
    return [int(i) for i in ids]


class AftercareCoordinator:
    def __init__(self,config,client,clock=time.time):
        self.config,self.client,self.clock=config,client,clock
        self.identity=_identity(config.identity)
        self._closed=threading.Event();self._tick_lock=threading.Lock()
        directory=Path(config.data_dir);directory.mkdir(parents=True,exist_ok=True)
        self.path=directory/'hunt-aftercare.sqlite3'
        with self._connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS aftercares(
                    id TEXT PRIMARY KEY,batch_request_id TEXT UNIQUE NOT NULL,
                    identity TEXT NOT NULL,authorization TEXT NOT NULL,enabled INTEGER NOT NULL,
                    created_at REAL NOT NULL,excluded TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aftercare_jobs(
                    id TEXT PRIMARY KEY,aftercare_id TEXT NOT NULL,attack_id TEXT UNIQUE NOT NULL,state TEXT NOT NULL);
            ''')

    @contextmanager
    def _connect(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:yield db
        finally:db.close()

    @contextmanager
    def _source(self,name):
        path=Path(self.config.data_dir).resolve()/name
        try:db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        except sqlite3.Error as error:raise ValueError('aftercare_source_unavailable') from error
        db.row_factory=sqlite3.Row
        try:yield db
        finally:db.close()

    def _get_job(self,job_id,db=None):
        if db is None:
            with self._connect() as connection:return self._get_job(job_id,connection)
        row=db.execute('SELECT state FROM aftercare_jobs WHERE id=?',(job_id,)).fetchone()
        if row is None:raise ValueError('aftercare_job_not_found')
        return json.loads(row['state'])

    def _save_job(self,db,state):
        db.execute('UPDATE aftercare_jobs SET state=? WHERE id=?',(_encode(state),state['id']))

    def _update(self,job_id,**changes):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');state=self._get_job(job_id,db);state.update(changes)
            self._save_job(db,state)
        return state

    def enroll(self,batch_request_id,authorization):
        if not isinstance(authorization,str) or not authorization.strip():
            raise ValueError('aftercare_authorization_required')
        if not isinstance(batch_request_id,str) or not batch_request_id.strip():
            raise ValueError('aftercare_batch_request_required')
        if _identity(self.config.identity)!=self.identity:raise ValueError('aftercare_identity_changed')
        with self._connect() as db:
            previous=db.execute('SELECT id,identity FROM aftercares WHERE batch_request_id=?',(batch_request_id,)).fetchone()
        if previous:
            if json.loads(previous['identity'])!=self.identity:raise ValueError('aftercare_identity_changed')
            return self.status(previous['id'])
        with self._source('hunt-dispatches.sqlite3') as db:
            row=db.execute('SELECT world,snapshot FROM dispatch_batches WHERE request_id=?',(batch_request_id,)).fetchone()
        if row is None:raise ValueError('aftercare_batch_not_found')
        batch=json.loads(row['snapshot'])
        if (row['world']!=self.identity['world'] or batch.get('world')!=row['world']
                or batch.get('request_id')!=batch_request_id or not isinstance(batch.get('armies'),list)):
            raise ValueError('aftercare_batch_binding_invalid')
        rows=batch['armies'];ids=[a.get('army_id') for a in rows if isinstance(a,dict)]
        if len(ids)!=len(rows) or any(not isinstance(a,str) or not a.isdigit() for a in ids) or len(set(ids))!=len(ids):
            raise ValueError('aftercare_batch_armies_invalid')
        identity='aftercare:'+_hash([self.identity,batch_request_id]);jobs=[];excluded=[]
        with self._source('hunt-attacks.sqlite3') as db:
            for army in rows:
                command='dispatch:'+_hash([batch_request_id,army['army_id']])
                receipt=army.get('receipt') or {}
                record=db.execute('SELECT * FROM attacks WHERE id=?',(receipt.get('command_id'),)).fetchone()
                if record is None:raise ValueError('aftercare_attack_binding_invalid')
                record=dict(record);record['plan']=json.loads(record['plan'])
                record['result']=json.loads(record['result']) if record['result'] else None
                p,r=record['plan'],record['result'] or {}
                if (record['request_id']!=command or record['world']!=self.identity['world']
                        or record['army_id']!=army['army_id'] or p.get('world')!=record['world']
                        or p.get('army_id')!=record['army_id'] or not isinstance(p.get('target'),dict)
                        or record['target_id']!=p['target'].get('id')
                        or not isinstance(army.get('target'),dict) or army['target']['id']!=record['target_id']
                        or army.get('status') not in ('sent','unknown')):
                    raise ValueError('aftercare_attack_binding_invalid')
                _ids(p.get('heroes'))
                historical=[value['identity'] for value in (p,r) if 'identity' in value]
                historical += [event['identity'] for event in r.get('events',[]) if isinstance(event,dict) and 'identity' in event]
                if any(_identity(value)!=self.identity for value in historical):
                    raise ValueError('aftercare_historical_identity_mismatch')
                if (record['status']!='accepted' or r.get('status')!='accepted' or r.get('sent') is not True
                        or r.get('departure_verified') is not True):
                    excluded.append(dict(army_id=army['army_id'],reason='accepted_departure_required'));continue
                if not _number(record['claimed_at']) or not 0<record['claimed_at']<=self.clock():
                    raise ValueError('aftercare_claim_time_invalid')
                job_id='aftercare-job:'+_hash([identity,record['id']])
                jobs.append(dict(id=job_id,aftercare_id=identity,batch_request_id=batch_request_id,
                    world=self.identity['world'],identity=deepcopy(self.identity),army_id=army['army_id'],
                    attack_id=record['id'],historical_record=record,plan=deepcopy(p),
                    status='waiting_return',reason='await_return',return_witnesses=[],attempt=0,
                    refill_request_id=None,attempts=[],receipt=None,replenishing=False,refill_pending=False,
                    next_poll_at=self.clock(),created_at=self.clock()))
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT id FROM aftercares WHERE batch_request_id=?',(batch_request_id,)).fetchone()
            if previous:return self.status(previous['id'])
            db.execute('INSERT INTO aftercares VALUES(?,?,?,?,?,?,?)',
                (identity,batch_request_id,_encode(self.identity),authorization,1,self.clock(),_encode(excluded)))
            for state in jobs:
                db.execute('INSERT INTO aftercare_jobs VALUES(?,?,?,?)',(state['id'],identity,state['attack_id'],_encode(state)))
        return self.status(identity)

    def status(self,aftercare_id=None):
        with self._connect() as db:
            if aftercare_id is None:
                ids=[row[0] for row in db.execute('SELECT id FROM aftercares ORDER BY rowid')]
                return {'aftercares':[self.status(value) for value in ids]}
            row=db.execute('SELECT * FROM aftercares WHERE id=?',(aftercare_id,)).fetchone()
            if row is None:raise ValueError('aftercare_not_found')
            result=dict(row);result['aftercare_id']=result.pop('id');result['enabled']=bool(result['enabled'])
            result['identity']=json.loads(result['identity']);result['world']=result['identity']['world']
            result['excluded']=json.loads(result['excluded'])
            result['jobs']=[json.loads(value[0]) for value in db.execute(
                'SELECT state FROM aftercare_jobs WHERE aftercare_id=? ORDER BY rowid',(aftercare_id,))]
        jobs=result['jobs'];counts=dict(selected=len(jobs),complete=0,ready_for_next_attack=0,unknown=0,blocked=0,waiting=0)
        for state in jobs:
            key=state['status'] if state['status'] in ('complete','ready_for_next_attack','unknown','blocked') else 'waiting'
            counts[key]+=1
        result['counts']=counts
        result['status']=('complete' if jobs and all(s['status'] in DONE_STATES for s in jobs) else
                          'paused' if not result['enabled'] else 'blocked' if not jobs else
                          'unknown' if counts['unknown'] else 'active')
        return result

    def _active(self,state):
        if self._closed.is_set() or _identity(self.config.identity)!=self.identity:return False
        with self._connect() as db:
            row=db.execute('SELECT enabled,identity FROM aftercares WHERE id=?',(state['aftercare_id'],)).fetchone()
        return row is not None and row['enabled']==1 and json.loads(row['identity'])==self.identity

    def pause(self,aftercare_id=None):
        with self._connect() as db:
            if aftercare_id is None:db.execute('UPDATE aftercares SET enabled=0')
            elif db.execute('UPDATE aftercares SET enabled=0 WHERE id=?',(aftercare_id,)).rowcount!=1:
                raise ValueError('aftercare_not_found')
        return self.status(aftercare_id)

    def resume(self,aftercare_id=None):
        if self._closed.is_set():raise RuntimeError('aftercare_closed')
        with self._connect() as db:
            if aftercare_id is None:db.execute('UPDATE aftercares SET enabled=1')
            elif db.execute('UPDATE aftercares SET enabled=1 WHERE id=?',(aftercare_id,)).rowcount!=1:
                raise ValueError('aftercare_not_found')
            rows=db.execute('SELECT state FROM aftercare_jobs'+(' WHERE aftercare_id=?' if aftercare_id else ''),
                            (aftercare_id,) if aftercare_id else ()).fetchall()
            for row in rows:
                state=json.loads(row[0])
                if state['status'] in POLL_STATES:
                    state.update(status='waiting_return',return_witnesses=[],reason='resume_revalidate_return',next_poll_at=self.clock())
                    self._save_job(db,state)
        return self.status(aftercare_id)

    def close(self):self._closed.set()

    def _claim(self,state):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');current=self._get_job(state['id'],db)
            enabled=db.execute('SELECT enabled FROM aftercares WHERE id=?',(state['aftercare_id'],)).fetchone()
            if current['status']!='ready_for_refill' or not enabled[0] or self._closed.is_set():return None
            attempt=current['attempt']+1;request='aftercare-refill:'+_hash([current['id'],attempt])
            current.update(status='refill_claimed',attempt=attempt,refill_request_id=request,claimed_at=self.clock(),next_poll_at=self.clock())
            current['attempts'].append(dict(request_id=request,claimed_at=self.clock(),receipt=None))
            self._save_job(db,current)
        return current

    def _finish(self,state,receipt,cached=False):
        now=self.clock();state=self._get_job(state['id'])
        if state['attempts']:
            key='cached_receipt' if cached else 'receipt'
            if state['attempts'][-1].get(key) is None:state['attempts'][-1][key]=deepcopy(receipt)
        matching=(isinstance(receipt,dict) and receipt.get('world')==state['world']
            and receipt.get('army_id')==state['army_id'] and receipt.get('request_id')==state['refill_request_id'])
        full=False
        if matching and receipt.get('verified_full') is True and receipt.get('cleanup_verified') is True:
            try:
                full=(_ids(receipt.get('heroes'))==_ids(state['plan']['heroes'])
                    and all(type(h.get('troops')) is int and type(h.get('capacity')) is int
                            and 0<h['troops']==h['capacity']<=100000 for h in receipt['heroes'])
                    and ((receipt.get('status') in ('applied','complete') and receipt.get('sent') is True)
                         or receipt.get('status')=='already_full' and receipt.get('sent') is False))
            except ValueError:pass
        state.update(receipt=deepcopy(receipt),next_poll_at=now+30)
        if full:state.update(status='complete',reason='verified_per_hero_full',completed_at=now,refill_pending=False)
        elif (matching and receipt.get('status') in ('blocked','rejected','not_applied')
              and receipt.get('sent') is False and receipt.get('retryable') is True):
            state.update(status='waiting_return',reason=receipt.get('reason','known_unsent_retryable'),return_witnesses=[],refill_pending=False)
        elif matching and receipt.get('status') in ('blocked','rejected','not_applied') and receipt.get('sent') is False:
            state.update(status='blocked',reason=receipt.get('reason','known_unsent_not_retryable'),refill_pending=False)
        elif (matching and receipt.get('status') in ('accepted','queued','recruiting')
              and receipt.get('sent') is True and receipt.get('cleanup_verified') is True):
            state.update(status='waiting_recruitment',reason='existing_refill_queue:no_resend',refill_pending=True)
        else:state.update(status='unknown',reason='refill_outcome_unknown:no_resend',refill_pending=True)
        with self._connect() as db:self._save_job(db,state)

    def _ready(self,state,ready):
        now=self.clock()
        if (not isinstance(ready,dict) or ready.get('world')!=state['world'] or ready.get('army_id')!=state['army_id']
                or ready.get('cleanup_verified') is not True or ready.get('ready') is not True
                or not _number(ready.get('observed_at')) or not 0<=now-ready['observed_at']<=30
                or ready.get('recruitment_state_verified') is not True
                or any(type(ready.get(k)) is not bool for k in ('recruitment_active','wounded_recovery_pending',
                                                              'stamina_sufficient','orders_sufficient'))
                or _ids(ready.get('heroes'))!=_ids(state['plan']['heroes'])
                or any(type(h.get('troops')) is not int or type(h.get('capacity')) is not int
                       or not 0<=h['troops']<=h['capacity']<=100000 or h['capacity']<=0
                       or type(h.get('recovering')) is not bool for h in ready['heroes'])):
            raise ValueError('aftercare_ready_unverified')
        total=sum(h['troops'] for h in ready['heroes'])
        state=self._update(state['id'],ready_snapshot=deepcopy(ready),total_troops=total)
        if ready['recruitment_active']:phase,reason='waiting_recruitment','existing_queue_not_cancelled'
        elif ready['wounded_recovery_pending'] or any(h['recovering'] for h in ready['heroes']):
            phase,reason='waiting_recovery','wounded_recovery_pending'
        elif not ready['stamina_sufficient'] or not ready['orders_sufficient']:
            phase,reason='waiting_readiness','stamina_or_orders_insufficient'
        elif not state['replenishing'] and total>=MIN_TROOPS:
            self._update(state['id'],status='ready_for_next_attack',reason='threshold_met:dispatch_preflight_still_required',completed_at=now)
            return
        elif all(h['troops']==h['capacity'] for h in ready['heroes']):
            self._update(state['id'],status='complete',reason='verified_current_full_after_replenishment',
                         completion_witness=deepcopy(ready),completed_at=now,refill_pending=False)
            return
        elif state['refill_pending']:
            phase,reason='unknown','refill_goal_not_verified:no_resend'
        else:
            cap=self.client.capabilities()
            if cap.get('available') is not True:
                phase,reason='blocked_capability',';'.join(cap.get('missing') or ['refill_unavailable'])
            else:
                state=self._update(state['id'],status='ready_for_refill',reason='below_threshold_refill_to_capacity',
                                   replenishing=True)
                claimed=self._claim(state)
                if claimed is None:return
                if not self._active(claimed):
                    self._finish(claimed,dict(world=state['world'],army_id=state['army_id'],request_id=claimed['refill_request_id'],
                        status='blocked',sent=False,retryable=True,reason='stopped_before_refill_entry'));return
                try:receipt=self.client.refill(deepcopy(claimed),claimed['refill_request_id'])
                except Exception as error:receipt=dict(status='unknown',sent=None,reason=str(error))
                self._finish(claimed,receipt);return
        self._update(state['id'],status=phase,reason=reason,next_poll_at=now+30)

    def tick(self):
        if self._closed.is_set() or not self._tick_lock.acquire(blocking=False):return self.status()
        try:
            now=self.clock()
            with self._connect() as db:
                jobs=[json.loads(row[0]) for row in db.execute('''SELECT j.state FROM aftercare_jobs j
                    JOIN aftercares a ON a.id=j.aftercare_id WHERE a.enabled=1''')]
            jobs=[state for state in jobs if state['next_poll_at']<=now and self._active(state)]
            for state in jobs:
                if state['status'] not in ('refill_claimed','unknown'):continue
                try:receipt=self.client.cached_refill(deepcopy(state),state['refill_request_id'])
                except Exception:receipt=None
                if receipt is None:self._update(state['id'],status='unknown',reason='refill_cache_empty:no_resend',next_poll_at=self.clock()+30)
                else:self._finish(state,receipt,cached=True)
            pending=[self._get_job(state['id']) for state in jobs]
            pending=[state for state in pending if state['status'] in POLL_STATES and state['next_poll_at']<=self.clock() and self._active(state)]
            if not pending:return self.status()
            cap=self.client.capabilities()
            if cap.get('read_available') is not True:
                for state in pending:self._update(state['id'],reason='aftercare_read_unavailable',next_poll_at=self.clock()+30)
                return self.status()
            try:witness=self.client.observe_roster()
            except Exception as error:
                for state in pending:self._update(state['id'],reason='aftercare_observe_error:'+str(error),return_witnesses=[],next_poll_at=self.clock()+30)
                return self.status()
            for state in pending:
                if not self._active(state):continue
                now=self.clock()
                try:
                    row=_inactive_roster(state['historical_record'],witness,self.identity,now)
                    previous=state['return_witnesses'][-1] if state['return_witnesses'] else None
                    pair=False
                    if previous:
                        try:
                            old=_inactive_roster(state['historical_record'],previous,self.identity,now)
                            pair=(previous['runtime_final']['generation']!=witness['runtime_final']['generation']
                                and previous['client_session']==witness['client_session']
                                and 2<=witness['roster']['observed_at']-previous['roster']['observed_at']<=30
                                and 2<=row['observed_at']-old['observed_at']<=30)
                        except (ValueError,KeyError,TypeError):pass
                    if not pair:
                        self._update(state['id'],status='confirming_return',reason='second_independent_return_proof_required',
                            return_witnesses=[deepcopy(witness)],next_poll_at=now+2)
                        continue
                    state=self._update(state['id'],return_witnesses=[previous,deepcopy(witness)],reason='return_verified')
                except (ValueError,KeyError,TypeError) as error:
                    self._update(state['id'],status='waiting_return',reason=str(error),return_witnesses=[],next_poll_at=now+30)
                    continue
                if not self._active(state):continue
                try:self._ready(state,self.client.ready(deepcopy(state)))
                except Exception as error:
                    current=self._get_job(state['id'])
                    phase='unknown' if current['status']=='refill_claimed' else 'blocked_ready'
                    self._update(state['id'],status=phase,reason=str(error),next_poll_at=self.clock()+30)
            return self.status()
        finally:self._tick_lock.release()
