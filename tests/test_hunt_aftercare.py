"""Offline aftercare journal and status-only return proof contracts."""
from copy import deepcopy
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest


IDENTITY=dict(world='w',role_id='42',region='r',api_server_id='s',api_season=1)


def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def sources(directory, armies=('123',)):
    records=[];batch=dict(request_id='round',world='w',armies=[])
    with sqlite3.connect(directory/'hunt-attacks.sqlite3') as db:
        db.execute('CREATE TABLE attacks(id TEXT PRIMARY KEY,request_id TEXT,world TEXT,army_id TEXT,target_id TEXT,plan TEXT,status TEXT,claimed_at REAL,result TEXT,created_at REAL)')
        for index,army in enumerate(armies):
            plan=dict(world='w',army_id=army,base_id=str(101+index),return_base_id=str(101+index),
                position=[600+index*10,1500],heroes=[dict(id=str(index*10+i),capacity=10000) for i in (1,2,3)],
                target=dict(id=str(456+index),x=650,y=1550,use_code=1,conf_id=9,end_time=5000))
            command='dispatch:'+hashlib.sha256(encode(['round',army]).encode()).hexdigest()
            receipt=dict(status='accepted',sent=True,departure_verified=True,command_id='attack-'+army,events=[])
            record=dict(id='attack-'+army,request_id=command,world='w',army_id=army,target_id=plan['target']['id'],
                plan=plan,status='accepted',claimed_at=1000,result=receipt,created_at=999)
            db.execute('INSERT INTO attacks VALUES(?,?,?,?,?,?,?,?,?,?)',tuple(
                encode(record[key]) if key in ('plan','result') else record[key]
                for key in ('id','request_id','world','army_id','target_id','plan','status','claimed_at','result','created_at')))
            records.append(record)
            batch['armies'].append(dict(army_id=army,status='sent',target=plan['target'],receipt=receipt))
    with sqlite3.connect(directory/'hunt-dispatches.sqlite3') as db:
        db.execute('CREATE TABLE dispatch_batches(request_id TEXT PRIMARY KEY,world TEXT,selection TEXT,snapshot TEXT)')
        db.execute('CREATE TABLE dispatch_reservations(world TEXT,kind TEXT,resource TEXT,request_id TEXT,army_id TEXT)')
        db.execute('INSERT INTO dispatch_batches VALUES(?,?,?,?)',('round','w','all',encode(batch)))
        for record in records:
            db.execute('INSERT INTO dispatch_reservations VALUES(?,?,?,?,?)',('w','target',record['target_id'],'round',record['army_id']))
    return records


class Client:
    def __init__(self,clock,records):
        self.clock=clock;self.records=records;self.observations=0;self.calls=[]
        self.phases={record['army_id']:1 for record in records}
        self.patch=None;self.reply=None;self.cached=None
        self.ready_patch=None
        self.cap=dict(read_available=True,available=True,missing=[])

    def capabilities(self):return deepcopy(self.cap)

    def observe_roster(self):
        self.calls.append(('observe',));self.observations+=1;now=self.clock['now'];rows=[]
        for record in self.records:
            p=record['plan'];code=self.phases[record['army_id']]
            rows.append(dict(army_id=int(record['army_id']),army_type=1,
                hero_ids=[int(h['id']) for h in p['heroes']],observed_at=now,status_codes=[code],
                army_status=code,system_status=code,position=p.get('dispatch_position',p['position']),
                dispatch_position=p.get('dispatch_position',p['position']),dispatch_build_id=int(p['base_id']),
                at_dispatch=True,dispatch_entered_at=900,movement_end_time=0,movement_total_time=0,
                command=None,destination=None,enemy_army_id=None,fight_position=None,is_tying=True))
        witness=dict(identity=deepcopy(IDENTITY),client_session=dict(pid=10,created_at=800),
            runtime_final=dict(generation='read-'+str(self.observations),phase='closed',fault=None,
                cleanup_fault=None,bridge_retained=False),roster=dict(complete=True,observed_at=now,armies=rows))
        if self.patch:self.patch(witness)
        return witness

    def refill(self,job,request_id):
        self.calls.append(('refill',job['army_id'],request_id,deepcopy(job)))
        if isinstance(self.reply,BaseException):raise self.reply
        return deepcopy(self.reply) if self.reply is not None else dict(world='w',army_id=job['army_id'],
            request_id=request_id,status='applied',sent=True,verified_full=True,cleanup_verified=True,
            heroes=[dict(id=h['id'],troops=12000,capacity=12000) for h in job['plan']['heroes']])

    def ready(self,job):
        self.calls.append(('ready',job['army_id']))
        result=dict(world='w',army_id=job['army_id'],observed_at=self.clock['now'],cleanup_verified=True,
            ready=True,recruitment_state_verified=True,recruitment_active=False,wounded_recovery_pending=False,
            stamina_sufficient=True,orders_sufficient=True,
            heroes=[dict(id=h['id'],troops=5000,capacity=12000,recovering=False) for h in job['plan']['heroes']])
        if self.ready_patch:self.ready_patch(result)
        return result

    def cached_refill(self,job,request_id):
        self.calls.append(('cached',job['army_id'],request_id))
        return deepcopy(self.cached)


@pytest.fixture
def env(tmp_path):
    from sgz_agent.hunt_aftercare import AftercareCoordinator
    records=sources(tmp_path);clock=dict(now=1005);client=Client(clock,records)
    config=SimpleNamespace(data_dir=tmp_path,identity=deepcopy(IDENTITY))
    care=AftercareCoordinator(config,client,clock=lambda:clock['now'])
    return care,client,clock,config,records


def enroll(env):return env[0].enroll('round',authorization='User requested return and full replenishment')
def job(care,identity):return care.status(identity)['jobs'][0]


def returned_twice(env):
    care,client,clock,_,_=env;batch=enroll(env)
    clock['now']=1010;care.tick()
    clock['now']=1012;care.tick()
    return batch['aftercare_id']


def test_enrollment_freezes_history_and_never_changes_original_receipts_or_target_fences(env):
    care,client,clock,config,records=env
    with sqlite3.connect(config.data_dir/'hunt-attacks.sqlite3') as db:
        original=db.execute('SELECT result FROM attacks').fetchone()[0]
    batch=enroll(env)
    assert len(batch['jobs'])==1 and batch['jobs'][0]['historical_record']==records[0]
    assert batch['jobs'][0]['plan']==records[0]['plan'] and client.calls==[]
    assert enroll(env)==batch
    assert returned_twice(env)==batch['aftercare_id']
    assert job(care,batch['aftercare_id'])['status']=='complete'
    with sqlite3.connect(config.data_dir/'hunt-attacks.sqlite3') as db:
        assert db.execute('SELECT result FROM attacks').fetchone()[0]==original
        assert db.execute('SELECT status FROM attacks').fetchone()[0]=='accepted'
    with sqlite3.connect(config.data_dir/'hunt-dispatches.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM dispatch_reservations').fetchone()[0]==1


def test_authorization_is_required_and_unknown_departure_is_not_enrolled(env):
    care,client,clock,config,records=env
    with pytest.raises(ValueError,match='authorization'):care.enroll('round',authorization=' ')
    with sqlite3.connect(config.data_dir/'hunt-attacks.sqlite3') as db:
        db.execute("UPDATE attacks SET status='unknown'")
    batch=enroll(env)
    assert batch['jobs']==[] and batch['excluded'][0]['army_id']=='123'


@pytest.mark.parametrize('mutation',['world','request','command','target','formation','historical_identity'])
def test_enrollment_rejects_mismatched_original_binding(env,mutation):
    care,client,clock,config,records=env
    with sqlite3.connect(config.data_dir/'hunt-attacks.sqlite3') as db:
        if mutation=='world':db.execute("UPDATE attacks SET world='other'")
        elif mutation=='request':db.execute("UPDATE attacks SET request_id='wrong'")
        elif mutation=='command':db.execute("UPDATE attacks SET id='wrong'")
        elif mutation=='target':db.execute("UPDATE attacks SET target_id='wrong'")
        elif mutation=='formation':
            plan=deepcopy(records[0]['plan']);plan['heroes'][1]['id']='1'
            db.execute('UPDATE attacks SET plan=?',(encode(plan),))
        else:
            receipt=deepcopy(records[0]['result']);receipt['events']=[dict(identity={**IDENTITY,'role_id':'43'})]
            db.execute('UPDATE attacks SET result=?',(encode(receipt),))
    with pytest.raises(ValueError,match='aftercare'):enroll(env)


def test_two_independent_fresh_return_statuses_precede_any_refill(env):
    care,client,clock,_,_=env;batch=enroll(env);identity=batch['aftercare_id']
    clock['now']=1010;care.tick()
    assert job(care,identity)['status']=='confirming_return'
    assert not any(call[0]=='refill' for call in client.calls)
    clock['now']=1011;care.tick();assert client.observations==1
    clock['now']=1012;care.tick()
    state=job(care,identity)
    assert state['status']=='complete' and len(state['return_witnesses'])==2
    assert state['attempt']==1 and state['receipt']['heroes'][0]['capacity']==12000
    assert len([call for call in client.calls if call[0]=='refill'])==1


@pytest.mark.parametrize('code',[2,4,24,25,26,99])
def test_moving_fighting_draw_or_ambiguous_army_has_no_refill_or_details(env,code):
    care,client,clock,_,_=env;batch=enroll(env);client.phases['123']=code
    clock['now']=1010;care.tick();clock['now']=1040;care.tick()
    assert not any(call[0]=='refill' for call in client.calls)
    assert job(care,batch['aftercare_id'])['return_witnesses']==[]


@pytest.mark.parametrize('mutation',['identity','open_runtime','same_generation','other_process','stale',
    'wrong_base','wrong_position','wrong_heroes','duplicate_army','missing_command','enemy','partial_roster'])
def test_invalid_second_return_witness_never_authorizes_refill(env,mutation):
    care,client,clock,_,_=env;batch=enroll(env);clock['now']=1010;care.tick()
    def patch(w):
        row=w['roster']['armies'][0]
        if mutation=='identity':w['identity']['role_id']='43'
        elif mutation=='open_runtime':w['runtime_final']['phase']='running'
        elif mutation=='same_generation':w['runtime_final']['generation']='read-1'
        elif mutation=='other_process':w['client_session']['pid']=11
        elif mutation=='stale':w['roster']['observed_at']=row['observed_at']=970
        elif mutation=='wrong_base':row['dispatch_build_id']=999
        elif mutation=='wrong_position':row['position']=[601,1500]
        elif mutation=='wrong_heroes':row['hero_ids']=[2,1,3]
        elif mutation=='duplicate_army':w['roster']['armies'].append(deepcopy(row))
        elif mutation=='missing_command':row.pop('command')
        elif mutation=='enemy':row['enemy_army_id']=9
        else:w['roster']['complete']=False
    client.patch=patch;clock['now']=1012;care.tick()
    assert not any(call[0]=='refill' for call in client.calls)
    assert job(care,batch['aftercare_id'])['status']!='complete'


def test_a_returned_army_progresses_while_b_is_still_fighting(tmp_path):
    from sgz_agent.hunt_aftercare import AftercareCoordinator
    records=sources(tmp_path,('123','124'));clock=dict(now=1005);client=Client(clock,records)
    client.phases['124']=24
    care=AftercareCoordinator(SimpleNamespace(data_dir=tmp_path,identity=IDENTITY),client,clock=lambda:clock['now'])
    batch=care.enroll('round',authorization='Return and refill both')
    clock['now']=1010;care.tick();clock['now']=1012;care.tick()
    states={state['army_id']:state for state in care.status(batch['aftercare_id'])['jobs']}
    assert states['123']['status']=='complete' and states['124']['status']!='complete'
    assert [call[1] for call in client.calls if call[0]=='refill']==['123']


def test_claimed_crash_restarts_with_cache_only_and_empty_cache_becomes_unknown(env):
    from sgz_agent.hunt_aftercare import AftercareCoordinator
    care,client,clock,config,_=env;batch=enroll(env)
    clock['now']=1010;care.tick();client.reply=KeyboardInterrupt('process death')
    clock['now']=1012
    with pytest.raises(KeyboardInterrupt):care.tick()
    assert job(care,batch['aftercare_id'])['status']=='refill_claimed'
    restarted=AftercareCoordinator(config,client,clock=lambda:clock['now'])
    restarted.tick()
    assert job(restarted,batch['aftercare_id'])['status']=='unknown'
    clock['now']=1050;client.reply=None;restarted.tick()
    assert len([call for call in client.calls if call[0]=='refill'])==1


@pytest.mark.parametrize('mutation',['world','army','request','order','not_full','unverified','unclean'])
def test_only_matching_verified_per_hero_full_receipt_completes(env,mutation):
    care,client,clock,_,records=env;batch=enroll(env);clock['now']=1010;care.tick()
    def reply(job,request_id):
        result=dict(world='w',army_id='123',request_id=request_id,status='applied',sent=True,
            verified_full=True,cleanup_verified=True,heroes=[dict(id=h['id'],troops=10000,capacity=10000) for h in records[0]['plan']['heroes']])
        if mutation=='world':result['world']='other'
        elif mutation=='army':result['army_id']='124'
        elif mutation=='request':result['request_id']='other'
        elif mutation=='order':result['heroes'].reverse()
        elif mutation=='not_full':result['heroes'][0]['troops']=9999
        elif mutation=='unverified':result['verified_full']=False
        else:result['cleanup_verified']=False
        return result
    client.refill=reply;clock['now']=1012;care.tick()
    assert job(care,batch['aftercare_id'])['status']=='unknown'


def test_pause_survives_restart_and_normal_close_does_not_pause_authorization(env):
    from sgz_agent.hunt_aftercare import AftercareCoordinator
    care,client,clock,config,_=env;batch=enroll(env);identity=batch['aftercare_id']
    care.pause(identity);care.close()
    reopened=AftercareCoordinator(config,client,clock=lambda:clock['now']);reopened.tick()
    assert reopened.status(identity)['status']=='paused' and client.calls==[]
    reopened.resume(identity);reopened.close()
    restored=AftercareCoordinator(config,client,clock=lambda:clock['now'])
    clock['now']=1010;restored.tick()
    assert restored.status(identity)['enabled'] is True and client.observations==1


def test_known_unsent_retryable_requires_a_new_return_pair_and_new_request_id(env):
    care,client,clock,_,_=env;batch=enroll(env);clock['now']=1010;care.tick()
    first=client.refill
    def blocked(job,request_id):
        client.calls.append(('refill',job['army_id'],request_id,deepcopy(job)))
        return dict(world='w',army_id=job['army_id'],request_id=request_id,status='blocked',sent=False,
                    retryable=True,reason='reserve_insufficient')
    client.refill=blocked;clock['now']=1012;care.tick()
    assert job(care,batch['aftercare_id'])['return_witnesses']==[]
    client.refill=first;clock['now']=1042;care.tick();clock['now']=1044;care.tick()
    calls=[call for call in client.calls if call[0]=='refill']
    assert len(calls)==2 and calls[0][2]!=calls[1][2]
    assert job(care,batch['aftercare_id'])['status']=='complete'


@pytest.mark.parametrize('base_kind',['city','camp'])
@pytest.mark.parametrize('troops,expected',[(19999,'complete'),(20000,'ready_for_next_attack'),(27000,'ready_for_next_attack')])
def test_only_below_twenty_thousand_starts_new_replenishment_in_either_base(env,base_kind,troops,expected):
    care,client,clock,config,records=env
    def patch(row):
        pieces=[troops//3,troops//3,troops-2*(troops//3)]
        for hero,value in zip(row['heroes'],pieces):hero['troops']=value
        row['base_kind']=base_kind
    client.ready_patch=patch
    identity=returned_twice(env)
    assert job(care,identity)['status']==expected
    assert len([call for call in client.calls if call[0]=='refill'])==(1 if troops<20000 else 0)


@pytest.mark.parametrize('troops',[15000,27000])
def test_existing_recruitment_queue_is_waited_for_without_cancel_or_new_refill(env,troops):
    care,client,clock,_,_=env
    def patch(row):
        row['recruitment_active']=True
        for hero in row['heroes']:hero['troops']=troops//3
    client.ready_patch=patch
    identity=returned_twice(env)
    assert job(care,identity)['status']=='waiting_recruitment'
    assert not any(call[0]=='refill' for call in client.calls)


@pytest.mark.parametrize('field',['cleanup_verified','ready','observed_at','recruitment_state_verified',
    'recruitment_active','wounded_recovery_pending','stamina_sufficient','orders_sufficient'])
def test_missing_ready_fields_cannot_authorize_a_refill_or_next_attack(env,field):
    care,client,clock,_,_=env;client.ready_patch=lambda row:row.pop(field)
    identity=returned_twice(env)
    assert job(care,identity)['status'] not in ('complete','ready_for_next_attack')
    assert not any(call[0]=='refill' for call in client.calls)


def test_entered_low_troop_replenishment_remains_full_goal_after_retry(env):
    care,client,clock,_,_=env;batch=enroll(env);clock['now']=1010;care.tick()
    original=client.refill
    client.refill=lambda state,rid:dict(world='w',army_id=state['army_id'],request_id=rid,
        status='blocked',sent=False,retryable=True,reason='reserve_insufficient')
    clock['now']=1012;care.tick()
    assert job(care,batch['aftercare_id'])['replenishing'] is True
    def high(row):
        for hero in row['heroes']:hero['troops']=9000
    client.ready_patch=high;client.refill=original
    clock['now']=1042;care.tick();clock['now']=1044;care.tick()
    assert job(care,batch['aftercare_id'])['status']=='complete'


def test_receipt_persistence_failure_after_claim_never_becomes_retryable_readiness(env):
    care,client,clock,_,_=env;batch=enroll(env);clock['now']=1010;care.tick()
    original=client.refill
    def unserializable(state,request_id):
        receipt=original(state,request_id);receipt['invalid_extra']={1,2}
        return receipt
    client.refill=unserializable;clock['now']=1012;care.tick()
    assert job(care,batch['aftercare_id'])['status']=='unknown'
    clock['now']=1042;care.tick()
    assert len([call for call in client.calls if call[0]=='refill'])==1


def test_low_troops_waiting_for_wounds_do_not_latch_a_new_refill_when_recovery_restores_threshold(env):
    care,client,clock,_,_=env
    client.ready_patch=lambda ready:ready.update(wounded_recovery_pending=True)
    identity=returned_twice(env)
    assert job(care,identity)['status']=='waiting_recovery'
    assert job(care,identity)['replenishing'] is False
    def recovered(ready):
        for hero in ready['heroes']:hero['troops']=9000
    client.ready_patch=recovered
    clock['now']=1042;care.tick();clock['now']=1044;care.tick()
    assert job(care,identity)['status']=='ready_for_next_attack'
    assert not any(call[0]=='refill' for call in client.calls)
