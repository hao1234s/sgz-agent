import copy
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest


def row(identity='11', **changes):
    return {'army_id':identity,'phase':'idle','home_city_id':'10','current_base_id':'10',
            'ordinary_verified':True,'recruitment_clear':True,'reserve':10000,
            'heroes':[{'id':'101','troops':2000,'capacity':5000},
                      {'id':'102','troops':3000,'capacity':5000}],**changes}


class Client:
    def __init__(self, rows=None):
        self.rows=rows or [row()]
        self.calls=[]
        self.failure=False

    def snapshot(self, army_ids=None):
        self.calls.append(('snapshot',army_ids))
        return {'complete':True,'armies':copy.deepcopy(self.rows)}

    def execute(self, plan):
        self.calls.append(('execute',copy.deepcopy(plan)))
        if self.failure:raise TimeoutError('lost_response')
        return {'status':'applied','sent':True,'army_id':plan['army_id'],
                'heroes':[{'id':h['id'],'troops':h['capacity'],'capacity':h['capacity']} for h in plan['heroes']],
                'army_ack':True,'response_ack':True}


def service(tmp_path,client=None):
    from sgz_agent.replenish import Replenisher
    return Replenisher(SimpleNamespace(data_dir=tmp_path,identity={'world':'w'}),client=client or Client())


def test_replenish_sends_full_totals_once_and_persists_result(tmp_path):
    client=Client()
    worker=service(tmp_path,client)
    result=worker.run(world='w',request_id='one',army_ids=['11'])
    assert result['armies'][0]['status']=='applied'
    command=[c[1] for c in client.calls if c[0]=='execute'][0]
    assert command['request']=={'army_id':11,'hero_id_list':[101,102],'hero_troop_list':[5000,5000],'cons_index':1}
    reopened=service(tmp_path,Client())
    assert reopened.run(world='w',request_id='one',army_ids=['11'])==result
    assert reopened.client.calls==[]


@pytest.mark.parametrize('changes,reason',[
    ({'phase':'fighting','heroes':None},'army_not_idle'),
    ({'phase':'draw','heroes':None},'army_not_idle'),
    ({'ordinary_verified':False},'ordinary_reserve_unverified'),
    ({'current_base_id':'12'},'not_in_own_city'),
    ({'reserve':4999},'reserve_insufficient'),
    ({'recruitment_clear':False},'recruitment_state_unverified'),
])
def test_unsupported_or_unready_army_is_never_sent(tmp_path,changes,reason):
    client=Client([row(**changes)])
    result=service(tmp_path,client).run(world='w',request_id='guard')
    assert result['armies'][0]['reason']==reason
    assert not any(c[0]=='execute' for c in client.calls)


def test_dry_run_does_not_send_or_consume_request_id(tmp_path):
    client=Client();worker=service(tmp_path,client)
    preview=worker.run(world='w',request_id='one',dry_run=True)
    assert preview['armies'][0]['status']=='ready'
    assert not any(c[0]=='execute' for c in client.calls)
    assert worker.run(world='w',request_id='one')['armies'][0]['status']=='applied'


def test_already_full_is_a_noop(tmp_path):
    client=Client([row(heroes=[{'id':'101','troops':5000,'capacity':5000}])])
    assert service(tmp_path,client).run(world='w',request_id='one')['armies'][0]['status']=='already_full'
    assert not any(c[0]=='execute' for c in client.calls)


def test_uncertain_send_blocks_retries_after_restart_and_with_new_id(tmp_path):
    client=Client();client.failure=True
    first=service(tmp_path,client).run(world='w',request_id='lost')
    assert first['armies'][0]['status']=='unknown'
    fresh=Client();worker=service(tmp_path,fresh)
    assert worker.run(world='w',request_id='lost')==first
    second=worker.run(world='w',request_id='different')
    assert second['armies'][0]['reason']=='previous_command_unknown'
    assert not any(c[0]=='execute' for c in fresh.calls)


def test_request_reuse_with_different_armies_is_rejected(tmp_path):
    worker=service(tmp_path)
    worker.run(world='w',request_id='one',army_ids=['11'])
    with pytest.raises(ValueError,match='request_conflict'):
        worker.run(world='w',request_id='one',army_ids=['12'])


def test_wrong_world_rejected_before_observing_client(tmp_path):
    worker=service(tmp_path)
    with pytest.raises(ValueError,match='world_mismatch'):
        worker.run(world='wrong',request_id='one')
    assert worker.client.calls==[]


def test_acknowledgement_without_actual_full_troops_stays_unknown(tmp_path):
    client=Client()
    client.execute=lambda plan:{'status':'applied','sent':True,'army_ack':True,'response_ack':True,'heroes':row()['heroes']}
    result=service(tmp_path,client).run(world='w',request_id='one')
    assert result['armies'][0]['status']=='unknown'


def test_unknown_preserves_native_cause_and_never_replays(tmp_path):
    client=Client();client.execute=lambda plan:{'status':'unknown','sent':True,
        'reason':'replenish_correlation_ambiguous','encoded':0,'army_ack':None,'response_ack':None}
    result=service(tmp_path,client).run(world='w',request_id='one')
    assert result['armies'][0]['reason']=='replenish_correlation_ambiguous'
    assert result['armies'][0]['encoded']==0


def test_snapshot_must_not_omit_explicit_army_or_claim_full_selection_when_incomplete(tmp_path):
    worker=service(tmp_path)
    with pytest.raises(ValueError,match='army_not_owned_or_missing'):
        worker.run(world='w',request_id='one',army_ids=['12'])
    worker.client.snapshot=lambda **kwargs:{'complete':False,'armies':[row()]}
    with pytest.raises(RuntimeError,match='roster_incomplete'):
        worker.run(world='w',request_id='all')


@pytest.mark.parametrize('state,command',[({'encounter_open':True},False),({},True)])
def test_stopped_hunt_with_unresolved_battle_or_command_still_blocks_read(tmp_path,state,command):
    from sgz_agent.hunt_store import HuntStore,encode
    store=HuntStore(tmp_path)
    with store.db:
        store.db.execute('INSERT INTO hunts VALUES(?,?,?,?,?,?,?,?,?)',
                         ('h','request','w','stopped','{}','all',0,0,None))
        store.db.execute('INSERT INTO hunt_armies VALUES(?,?,?)',('h','11',encode(state)))
        if command:store.db.execute('INSERT INTO hunt_commands VALUES(?,?,?,?,?,?,?,?)',
                                     ('c','h','11','attack','{}','unknown',0,None))
    store.close()
    worker=service(tmp_path)
    with pytest.raises(RuntimeError,match='hunt_owns_army'):
        worker.run(world='w',request_id='refill')
    assert worker.client.calls==[]


class ReconcileClient(Client):
    def __init__(self, rows=None, *, identity=None):
        super().__init__(rows)
        self.identity = identity or {'world':'w'}
        self.snapshots = []
        self.observed = 0

    def snapshot(self, army_ids=None):
        self.calls.append(('snapshot',army_ids))
        if self.snapshots:
            return copy.deepcopy(self.snapshots.pop(0))
        self.observed=max(time.time(),self.observed+0.001)
        return {'complete':True, 'identity':copy.deepcopy(self.identity),
                'observed_at':self.observed, 'armies':copy.deepcopy(self.rows),
                'native_lifecycle':{'closed':True,'active_native':0,'callbacks':0,'hooks':0},
                'cleanup_verified':True}


def unresolved_extension(tmp_path):
    client=ReconcileClient();client.failure=True
    original=service(tmp_path,client).run(world='w',request_id='old',army_ids=['11'])
    client.failure=False
    client.rows=[row(heroes=[{'id':'101','troops':5000,'capacity':5000,'recovering':False},
                            {'id':'102','troops':5000,'capacity':5000,'recovering':False},
                            {'id':'103','troops':0,'capacity':5000,'recovering':False}])]
    return service(tmp_path,client),client,original


def journal_rows(tmp_path):
    with sqlite3.connect(tmp_path/'replenish'/'commands.sqlite3') as db:
        return (db.execute('SELECT id,request_id,world,army_id,status,payload,result FROM commands ORDER BY rowid').fetchall(),
                db.execute('SELECT * FROM requests ORDER BY rowid').fetchall())


def test_reconciliation_preserves_history_and_only_allows_new_compatible_extension(tmp_path):
    worker,client,original=unresolved_extension(tmp_path)
    before=journal_rows(tmp_path)
    command_id=original['armies'][0]['command_id']
    resolution=worker.reconcile(world='w',command_id=command_id)
    assert resolution['status']=='allow_compatible_extension'
    assert resolution['sent'] is False
    assert journal_rows(tmp_path)==before
    snapshots_before=len([c for c in client.calls if c[0]=='snapshot'])
    calls_before=len(client.calls)
    assert worker.run(world='w',request_id='old',army_ids=['11'])==original
    assert len(client.calls)==calls_before
    assert snapshots_before==3
    outcome=service(tmp_path,client).run(world='w',request_id='extension',army_ids=['11'])
    assert outcome['armies'][0]['status']=='applied'
    assert client.calls[-1][1]['request']=={'army_id':11,'hero_id_list':[101,102,103],
                                            'hero_troop_list':[5000,5000,5000],'cons_index':1}
    commands,requests=journal_rows(tmp_path)
    assert commands[0]==before[0][0]
    assert requests[0]==before[1][0]
    with sqlite3.connect(tmp_path/'replenish'/'commands.sqlite3') as db:
        evidence=json.loads(db.execute('SELECT evidence FROM resolutions WHERE id=?',
                                      (resolution['resolution_id'],)).fetchone()[0])
    assert evidence['scope']['command_id']==command_id
    assert evidence['scope']['identity']=={'world':'w'}
    assert evidence['observations'][0]['observed_at']<evidence['observations'][1]['observed_at']
    assert evidence['observations'][1]['armies'][0]['heroes'][2]['troops']==0
    assert evidence['historical_outcome']=='unknown'
    assert evidence['transport_quiescence_claimed'] is False


@pytest.mark.parametrize('change',[
    'old_hero_missing','old_hero_deficit','old_capacity_changed','home_changed',
])
def test_resolution_cannot_bypass_current_compatibility_guard(tmp_path,change):
    worker,client,original=unresolved_extension(tmp_path)
    worker.reconcile(world='w',command_id=original['armies'][0]['command_id'])
    current=client.rows[0]
    if change=='old_hero_missing':current['heroes'][0]['id']='104'
    if change=='old_hero_deficit':current['heroes'][0]['troops']=4999
    if change=='old_capacity_changed':current['heroes'][0].update(troops=6000,capacity=6000)
    if change=='home_changed':current.update(home_city_id='20',current_base_id='20')
    before=len([c for c in client.calls if c[0]=='execute'])
    result=worker.run(world='w',request_id='bad-extension')
    assert result['armies'][0]['reason']=='previous_command_unknown'
    assert len([c for c in client.calls if c[0]=='execute'])==before


@pytest.mark.parametrize('change',[
    'stale','future','bool_time','nan_time','same_time','wrong_identity','incomplete',
    'missing_army','duplicate_army','duplicate_hero','old_deficit','capacity_changed',
    'not_idle','not_home','imperial','queue','wounded','cleanup_unverified',
])
def test_reconciliation_rejects_untrusted_or_unsatisfied_observations(tmp_path,change):
    worker,client,original=unresolved_extension(tmp_path)
    command_id=original['armies'][0]['command_id']
    before=journal_rows(tmp_path)
    first=client.snapshot();second=client.snapshot()
    if change=='stale':second['observed_at']=time.time()-31
    if change=='future':second['observed_at']=time.time()+10
    if change=='bool_time':second['observed_at']=True
    if change=='nan_time':second['observed_at']=float('nan')
    if change=='same_time':second['observed_at']=first['observed_at']
    if change=='wrong_identity':second['identity']={'world':'other'}
    if change=='incomplete':second['complete']=False
    if change=='missing_army':second['armies']=[]
    if change=='duplicate_army':second['armies']*=2
    if change=='duplicate_hero':second['armies'][0]['heroes'][0]['id']='102'
    if change=='old_deficit':second['armies'][0]['heroes'][0]['troops']=4999
    if change=='capacity_changed':second['armies'][0]['heroes'][0].update(troops=6000,capacity=6000)
    if change=='not_idle':second['armies'][0]['phase']='fighting'
    if change=='not_home':second['armies'][0]['current_base_id']='20'
    if change=='imperial':second['armies'][0]['ordinary_verified']=False
    if change=='queue':second['armies'][0]['recruitment_clear']=False
    if change=='wounded':second['armies'][0]['heroes'][0]['recovering']=True
    if change=='cleanup_unverified':second['cleanup_verified']=False
    client.snapshots=[first,second]
    calls_before=len(client.calls)
    result=worker.reconcile(world='w',command_id=command_id)
    assert result['status']=='blocked'
    assert result['sent'] is False
    assert journal_rows(tmp_path)==before
    assert not any(c[0]=='execute' for c in client.calls[calls_before:])


def test_new_unknown_extension_still_blocks_later_requests(tmp_path):
    worker,client,original=unresolved_extension(tmp_path)
    worker.reconcile(world='w',command_id=original['armies'][0]['command_id'])
    client.failure=True
    second=worker.run(world='w',request_id='extension')
    assert second['armies'][0]['status']=='unknown'
    assert journal_rows(tmp_path)[0][0][4]=='unknown'
    client.failure=False
    before=len([c for c in client.calls if c[0]=='execute'])
    third=service(tmp_path,client).run(world='w',request_id='extension-again')
    assert third['armies'][0]['reason']=='previous_command_unknown'
    assert len([c for c in client.calls if c[0]=='execute'])==before


def test_resolution_does_not_authorize_other_world_or_mismatched_payload(tmp_path):
    worker,client,original=unresolved_extension(tmp_path)
    command_id=original['armies'][0]['command_id']
    with pytest.raises(ValueError,match='world_mismatch'):
        worker.reconcile(world='other',command_id=command_id)
    resolution=worker.reconcile(world='w',command_id=command_id)
    with sqlite3.connect(tmp_path/'replenish'/'commands.sqlite3') as db:
        evidence=json.loads(db.execute('SELECT evidence FROM resolutions WHERE id=?',
                                      (resolution['resolution_id'],)).fetchone()[0])
        evidence['scope']['payload_sha256']='0'*64
        db.execute('UPDATE resolutions SET evidence=? WHERE id=?',(json.dumps(evidence),resolution['resolution_id']))
    result=worker.run(world='w',request_id='tampered-resolution')
    assert result['armies'][0]['reason']=='previous_command_unknown'


def test_missing_execution_validation_does_not_journal_phantom_unknown_or_observe(tmp_path):
    client=Client()
    def refuse():raise RuntimeError('replenish_validation_required')
    client.ensure_execution_ready=refuse
    with pytest.raises(RuntimeError,match='replenish_validation_required'):
        service(tmp_path,client).run(world='w',request_id='unvalidated')
    assert client.calls==[]
    assert journal_rows(tmp_path)==([],[])


def test_cached_request_never_checks_new_execution_validation(tmp_path):
    client=Client();worker=service(tmp_path,client)
    original=worker.run(world='w',request_id='cached')
    def refuse():raise RuntimeError('replenish_validation_required')
    client.ensure_execution_ready=refuse
    before=len(client.calls)
    assert worker.run(world='w',request_id='cached')==original
    assert len(client.calls)==before


@pytest.mark.parametrize('change',['identity','stale','future','cleanup','lifecycle'])
def test_extension_rechecks_current_snapshot_trust_after_resolution(tmp_path,change):
    worker,client,original=unresolved_extension(tmp_path)
    worker.reconcile(world='w',command_id=original['armies'][0]['command_id'])
    snapshot=client.snapshot()
    if change=='identity':snapshot['identity']={'world':'w','role_id':'different'}
    if change=='stale':snapshot['observed_at']=time.time()-31
    if change=='future':snapshot['observed_at']=time.time()+10
    if change=='cleanup':snapshot['cleanup_verified']=False
    if change=='lifecycle':snapshot['native_lifecycle']['active_native']=1
    client.snapshots=[snapshot]
    before=len([c for c in client.calls if c[0]=='execute'])
    result=worker.run(world='w',request_id='untrusted-extension')
    assert result['armies'][0]['reason']=='previous_command_unknown'
    assert len([c for c in client.calls if c[0]=='execute'])==before


def test_legacy_journal_migration_keeps_unknown_and_request_cache(tmp_path):
    directory=tmp_path/'replenish';directory.mkdir()
    payload=json.dumps({'army_id':'11','home_city_id':'10',
        'heroes':[{'id':'101','troops':0,'capacity':5000},{'id':'102','troops':0,'capacity':5000}],
        'request':{'army_id':11,'hero_id_list':[101,102],'hero_troop_list':[5000,5000],'cons_index':1}})
    outcome={'request_id':'legacy','world':'w','status':'needs_attention',
             'armies':[{'army_id':'11','status':'unknown','sent':True,'command_id':'legacy-command'}]}
    command=('legacy-command','legacy','w','11','unknown',payload,json.dumps(outcome['armies'][0]))
    request=('legacy',json.dumps({'selection':['11'],'world':'w'},sort_keys=True),json.dumps(outcome))
    with sqlite3.connect(directory/'commands.sqlite3') as db:
        db.executescript('''CREATE TABLE requests(id TEXT PRIMARY KEY,scope TEXT NOT NULL,result TEXT);
          CREATE TABLE commands(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,world TEXT NOT NULL,
            army_id TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,result TEXT);
          CREATE UNIQUE INDEX one_pending_per_army ON commands(world,army_id)
            WHERE status IN ('submitted','unknown','accepted');''')
        db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?)',command)
        db.execute('INSERT INTO requests VALUES(?,?,?)',request)
    client=ReconcileClient([row(heroes=[
        {'id':'101','troops':5000,'capacity':5000,'recovering':False},
        {'id':'102','troops':5000,'capacity':5000,'recovering':False},
        {'id':'103','troops':0,'capacity':5000,'recovering':False}])])
    worker=service(tmp_path,client)
    assert worker.run(world='w',request_id='legacy',army_ids=['11'])==outcome
    assert client.calls==[]
    assert journal_rows(tmp_path)==([command],[request])
    assert worker.reconcile(world='w',command_id='legacy-command')['status']=='allow_compatible_extension'
    assert worker.run(world='w',request_id='legacy-extension')['armies'][0]['status']=='applied'
    assert journal_rows(tmp_path)[0][0]==command
    assert journal_rows(tmp_path)[1][0]==request


def test_reconciliation_does_not_persist_a_first_observation_that_expired_during_second_read(tmp_path,monkeypatch):
    from sgz_agent import replenish
    worker,client,original=unresolved_extension(tmp_path)
    first=client.snapshot();second=client.snapshot()
    now=time.time()
    first['observed_at']=now
    second['observed_at']=now+0.01
    client.snapshots=[first,second]
    moments=iter([now,now,now+31])
    monkeypatch.setattr(replenish,'time',SimpleNamespace(time=lambda:next(moments)))
    result=worker.reconcile(world='w',command_id=original['armies'][0]['command_id'])
    assert result['status']=='blocked'
    with sqlite3.connect(tmp_path/'replenish'/'commands.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM resolutions').fetchone()[0]==0


def test_resolved_unknown_does_not_block_a_verified_already_full_noop_after_extension(tmp_path):
    worker,client,original=unresolved_extension(tmp_path)
    worker.reconcile(world='w',command_id=original['armies'][0]['command_id'])
    assert worker.run(world='w',request_id='extension')['armies'][0]['status']=='applied'
    client.rows[0]['heroes'][2]['troops']=5000
    before=len([c for c in client.calls if c[0]=='execute'])
    result=service(tmp_path,client).run(world='w',request_id='full-noop')
    assert result['armies'][0]['status']=='already_full'
    assert result['armies'][0]['sent'] is False
    assert result['armies'][0]['historical_unknown_command_ids']==[original['armies'][0]['command_id']]
    assert len([c for c in client.calls if c[0]=='execute'])==before
    assert journal_rows(tmp_path)[0][0][4]=='unknown'


def test_unresolved_unknown_still_blocks_full_troops_without_reconciliation(tmp_path):
    worker,client,original=unresolved_extension(tmp_path)
    client.rows[0]['heroes'][2]['troops']=5000
    before=len([c for c in client.calls if c[0]=='execute'])
    result=worker.run(world='w',request_id='unresolved-full')
    assert result['armies'][0]['reason']=='previous_command_unknown'
    assert len([c for c in client.calls if c[0]=='execute'])==before


def attest_native_execution(client):
    execute=client.execute
    def attested(plan):
        result=execute(plan)
        return {**result,'native_calls':1,'encoded':0,'ambiguous':False,'cleanup_verified':True,
                'native_lifecycle':{'closed':True,'active_native':0,'callbacks':0,'hooks':0}}
    client.execute=attested


def applied_extension(tmp_path, *, attested=True):
    worker,client,original=unresolved_extension(tmp_path)
    resolution=worker.reconcile(world='w',command_id=original['armies'][0]['command_id'])
    if attested:attest_native_execution(client)
    successor=worker.run(world='w',request_id='confirmed-extension')['armies'][0]
    assert successor['status']=='applied'
    return worker,client,original,resolution,successor


def test_confirmed_native_successor_retires_old_fence_for_repeated_battle_losses(tmp_path):
    worker,client,original,resolution,successor=applied_extension(tmp_path)
    history=journal_rows(tmp_path)
    client.rows[0]['heroes'][0]['troops']=3000
    client.rows[0]['heroes'][2]['troops']=4000
    result=service(tmp_path,client).run(world='w',request_id='after-battle')
    assert result['armies'][0]['status']=='applied'
    assert client.calls[-1][1]['request']['hero_troop_list']==[5000,5000,5000]
    assert journal_rows(tmp_path)[0][0]==history[0][0]
    assert journal_rows(tmp_path)[1][0]==history[1][0]
    before=len(client.calls)
    assert worker.run(world='w',request_id='old',army_ids=['11'])==original
    assert len(client.calls)==before
    with sqlite3.connect(tmp_path/'replenish'/'commands.sqlite3') as db:
        audit=db.execute('SELECT successor_command_id,evidence FROM confirmed_successors WHERE command_id=?',
                         (original['armies'][0]['command_id'],)).fetchone()
    assert audit[0]==successor['command_id']
    evidence=json.loads(audit[1])
    assert evidence['resolution_id']==resolution['resolution_id']
    assert evidence['identity']=={'world':'w'}
    assert evidence['historical_outcome']=='unknown'


@pytest.mark.parametrize('invalid',[
    'no_native_call','ambiguous','no_cleanup','no_army_ack','not_sent','wrong_army','unknown','noop',
])
def test_unproven_successor_does_not_retire_unknown_fence(tmp_path,invalid):
    worker,client,original=unresolved_extension(tmp_path)
    worker.reconcile(world='w',command_id=original['armies'][0]['command_id'])
    attest_native_execution(client)
    execute=client.execute
    def unproven(plan):
        result=execute(plan)
        if invalid=='no_native_call':result['native_calls']=0
        if invalid=='ambiguous':result['ambiguous']=True
        if invalid=='no_cleanup':result['cleanup_verified']=False
        if invalid=='no_army_ack':result['army_ack']=False
        if invalid=='not_sent':result['sent']=False
        if invalid=='wrong_army':result['army_id']='12'
        if invalid=='unknown':result['status']='unknown'
        if invalid=='noop':result.update(status='already_full',sent=False)
        return result
    client.execute=unproven
    successor=worker.run(world='w',request_id='unproven-extension')['armies'][0]
    confirmation=worker.confirm_successor(world='w',command_id=original['armies'][0]['command_id'],
                                         successor_command_id=successor['command_id'])
    assert confirmation['status']=='blocked'
    client.rows[0]['heroes'][0]['troops']=3000
    result=worker.run(world='w',request_id='still-blocked')
    assert result['armies'][0]['reason']=='previous_command_unknown'


def test_historical_successor_can_be_explicitly_confirmed_but_cached_calls_do_not_confirm(tmp_path):
    worker,client,original,resolution,successor=applied_extension(tmp_path,attested=False)
    with sqlite3.connect(tmp_path/'replenish'/'commands.sqlite3') as db:
        historical=json.loads(db.execute('SELECT result FROM commands WHERE id=?',(successor['command_id'],)).fetchone()[0])
        historical.update(native_calls=1,encoded=0,ambiguous=False,cleanup_verified=True,
            native_lifecycle={'closed':True,'active_native':0,'callbacks':0,'hooks':0})
        db.execute('UPDATE commands SET result=? WHERE id=?',(json.dumps(historical),successor['command_id']))
    before=len(client.calls)
    worker.run(world='w',request_id='confirmed-extension')
    assert len(client.calls)==before
    client.rows[0]['heroes'][0]['troops']=3000
    assert worker.run(world='w',request_id='before-explicit')['armies'][0]['reason']=='previous_command_unknown'
    confirmation=worker.confirm_successor(world='w',command_id=original['armies'][0]['command_id'],
                                         successor_command_id=successor['command_id'])
    assert confirmation['status']=='confirmed_successor'
    assert confirmation['sent'] is False
    assert worker.run(world='w',request_id='after-explicit')['armies'][0]['status']=='applied'


@pytest.mark.parametrize('change',['identity','base','capacity','hero_missing'])
def test_retired_fence_scope_cannot_be_reused_for_other_identity_or_targets(tmp_path,change):
    worker,client,original,resolution,successor=applied_extension(tmp_path)
    client.rows[0]['heroes'][0]['troops']=3000
    if change=='identity':worker.config.identity['role_id']='changed';client.identity={'world':'w','role_id':'changed'}
    if change=='base':client.rows[0].update(home_city_id='20',current_base_id='20')
    if change=='capacity':client.rows[0]['heroes'][0]['capacity']=6000
    if change=='hero_missing':client.rows[0]['heroes'][0]['id']='104'
    result=worker.run(world='w',request_id='different-scope')
    assert result['armies'][0]['reason']=='previous_command_unknown'


@pytest.mark.parametrize('change',['army','base','pool','target','resolution','identity','bool_troops','old_request'])
def test_explicit_confirmation_requires_same_scope_and_prior_resolution(tmp_path,change):
    worker,client,original,resolution,successor=applied_extension(tmp_path,attested=False)
    with sqlite3.connect(tmp_path/'replenish'/'commands.sqlite3') as db:
        payload,result=db.execute('SELECT payload,result FROM commands WHERE id=?',(successor['command_id'],)).fetchone()
        payload,result=json.loads(payload),json.loads(result)
        result.update(native_calls=1,encoded=0,ambiguous=False,cleanup_verified=True,
            native_lifecycle={'closed':True,'active_native':0,'callbacks':0,'hooks':0})
        if change=='army':db.execute('UPDATE commands SET army_id=? WHERE id=?',('12',successor['command_id']))
        if change=='old_request':db.execute('UPDATE commands SET request_id=? WHERE id=?',('old',successor['command_id']))
        if change=='base':payload['home_city_id']='20'
        if change=='pool':payload['request']['cons_index']=2
        if change=='target':payload['request']['hero_troop_list'][0]=6000
        if change=='bool_troops':payload['heroes'][2]['troops']=False
        if change=='resolution':
            db.execute('UPDATE commands SET extension_resolution_id=NULL WHERE id=?',(original['armies'][0]['command_id'],))
        if change=='identity':worker.config.identity['role_id']='other'
        db.execute('UPDATE commands SET payload=?,result=? WHERE id=?',
                   (json.dumps(payload),json.dumps(result),successor['command_id']))
    result=worker.confirm_successor(world='w',command_id=original['armies'][0]['command_id'],
                                    successor_command_id=successor['command_id'])
    assert result['status']=='blocked'
    with sqlite3.connect(tmp_path/'replenish'/'commands.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM confirmed_successors').fetchone()[0]==0


def test_new_unknown_after_retirement_still_blocks_retries(tmp_path):
    worker,client,original,resolution,successor=applied_extension(tmp_path)
    client.rows[0]['heroes'][0]['troops']=3000
    client.failure=True
    result=worker.run(world='w',request_id='lost-after-battle')
    assert result['armies'][0]['status']=='unknown'
    client.failure=False
    before=len([c for c in client.calls if c[0]=='execute'])
    assert worker.run(world='w',request_id='retry-after-battle')['armies'][0]['reason']=='previous_command_unknown'
    assert len([c for c in client.calls if c[0]=='execute'])==before


@pytest.mark.parametrize('change',['audit','successor_result','resolution'])
def test_tampered_retirement_evidence_fails_closed(tmp_path,change):
    worker,client,original,resolution,successor=applied_extension(tmp_path)
    with sqlite3.connect(tmp_path/'replenish'/'commands.sqlite3') as db:
        if change=='audit':db.execute("UPDATE confirmed_successors SET evidence='{}'")
        if change=='successor_result':
            result=json.loads(db.execute('SELECT result FROM commands WHERE id=?',(successor['command_id'],)).fetchone()[0])
            result['encoded']=2
            db.execute('UPDATE commands SET result=? WHERE id=?',(json.dumps(result),successor['command_id']))
        if change=='resolution':db.execute("UPDATE resolutions SET evidence='{}'")
    client.rows[0]['heroes'][0]['troops']=3000
    assert worker.run(world='w',request_id='tampered-retirement')['armies'][0]['reason']=='previous_command_unknown'


def pending_hunt_replenishment(tmp_path, client=None):
    from sgz_agent.hunt_store import HuntStore
    client=client or ReconcileClient([row(heroes=[
        {'id':'101','troops':2000,'capacity':5000,'recovering':False},
        {'id':'102','troops':3000,'capacity':5000,'recovering':False}])])
    store=HuntStore(tmp_path)
    campaign=store.create('hunt-request','w',['11'],['11'],{},time.time(),initial_statuses={'11':{'phase':'idle'}})
    command=store.prepare_command(campaign['id'],campaign['armies'][0],'replenish',
        {'base_id':'10','desired_total':10000,'expected_phase':'idle','heroes':copy.deepcopy(client.rows[0]['heroes'])},time.time())
    store.close()
    return service(tmp_path,client),client,campaign['id'],command['id']


def test_internal_hunt_replenishment_exempts_only_its_own_durable_ownership(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    with pytest.raises(RuntimeError,match='hunt_owns_army'):
        worker.run(world='w',request_id='standalone',army_ids=['11'])
    assert client.calls==[]
    result=worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert result['armies'][0]['status']=='applied'
    assert [c[0] for c in client.calls]==['snapshot','execute']
    assert client.calls[-1][1]['request']['hero_id_list']==[101,102]
    with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
        assert db.execute('SELECT status FROM hunt_commands WHERE id=?',(command_id,)).fetchone()[0]=='submitted'


@pytest.mark.parametrize('change',['paused','stopped','unknown','accepted','battle','pending','kind','world','other_hunt','other_unknown'])
def test_internal_hunt_replenishment_refuses_wrong_or_unsettled_ownership_before_read(tmp_path,change):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
        if change in {'paused','stopped'}:db.execute('UPDATE hunts SET status=? WHERE id=?',(change,hunt_id))
        if change in {'unknown','accepted'}:db.execute('UPDATE hunt_commands SET status=? WHERE id=?',(change,command_id))
        if change in {'battle','pending'}:
            state=json.loads(db.execute('SELECT state FROM hunt_armies WHERE hunt_id=?',(hunt_id,)).fetchone()[0])
            if change=='battle':state['encounter_open']=True
            if change=='pending':state['pending_command_id']='different-command'
            db.execute('UPDATE hunt_armies SET state=? WHERE hunt_id=?',(json.dumps(state),hunt_id))
        if change=='kind':db.execute("UPDATE hunt_commands SET kind='return' WHERE id=?",(command_id,))
        if change=='world':db.execute("UPDATE hunts SET world='other' WHERE id=?",(hunt_id,))
        if change in {'other_hunt','other_unknown'}:
            db.execute('INSERT INTO hunts VALUES(?,?,?,?,?,?,?,?,?)',
                       ('other','other-request','w','running' if change=='other_hunt' else 'stopped','{}','[]',0,0,None))
            db.execute('INSERT INTO hunt_armies VALUES(?,?,?)',('other','11',json.dumps({'army_id':'11','encounter_open':False})))
            if change=='other_unknown':
                db.execute('INSERT INTO hunt_commands VALUES(?,?,?,?,?,?,?,NULL)',
                           ('other-command','other','11','attack','{}','unknown',0))
    with pytest.raises((RuntimeError,ValueError)):
        worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert client.calls==[]


@pytest.mark.parametrize('change',['base','desired','hero_troops','hero_capacity','hero_missing','hero_duplicate','phase'])
def test_internal_hunt_replenishment_checks_entire_durable_payload_against_fresh_plan(tmp_path,change):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
        payload=json.loads(db.execute('SELECT payload FROM hunt_commands WHERE id=?',(command_id,)).fetchone()[0])
        if change=='base':payload['base_id']='20'
        if change=='desired':payload['desired_total']=9999
        if change=='hero_troops':payload['heroes'][0]['troops']=1999
        if change=='hero_capacity':payload['heroes'][0]['capacity']=6000
        if change=='hero_missing':payload['heroes'].pop()
        if change=='hero_duplicate':payload['heroes'][0]['id']='102'
        if change=='phase':payload['expected_phase']='stationed'
        db.execute('UPDATE hunt_commands SET payload=? WHERE id=?',(json.dumps(payload),command_id))
    with pytest.raises(RuntimeError,match='hunt_replenish_payload_mismatch'):
        worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert [c[0] for c in client.calls]==['snapshot']
    assert journal_rows(tmp_path)==([],[])


def test_internal_hunt_cached_request_remains_idempotent_after_stop_and_command_completion(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    first=worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
        db.execute("UPDATE hunts SET status='stopped' WHERE id=?",(hunt_id,))
        db.execute("UPDATE hunt_commands SET status='applied' WHERE id=?",(command_id,))
    fresh_client=Client()
    second=service(tmp_path,fresh_client).run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert second==first
    assert fresh_client.calls==[]


def test_internal_hunt_cached_scope_rejects_payload_tampering_without_replay(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    before=len(client.calls)
    with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
        payload=json.loads(db.execute('SELECT payload FROM hunt_commands WHERE id=?',(command_id,)).fetchone()[0])
        payload['desired_total']=9000
        db.execute('UPDATE hunt_commands SET payload=? WHERE id=?',(json.dumps(payload),command_id))
    with pytest.raises(ValueError,match='request_conflict'):
        worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert len(client.calls)==before


def test_internal_hunt_rechecks_ownership_after_snapshot_before_any_command_journal(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    snapshot=client.snapshot
    def stop_during_read(army_ids=None):
        result=snapshot(army_ids=army_ids)
        with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
            db.execute("UPDATE hunts SET status='paused' WHERE id=?",(hunt_id,))
        return result
    client.snapshot=stop_during_read
    with pytest.raises(RuntimeError):worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert [c[0] for c in client.calls]==['snapshot']
    assert journal_rows(tmp_path)==([],[])


def test_internal_hunt_known_unsent_ownership_rejection_has_a_typed_receipt(tmp_path):
    from sgz_agent.replenish import HuntReplenishmentBlocked
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:db.execute("UPDATE hunts SET status='paused'")
    with pytest.raises(HuntReplenishmentBlocked) as error:
        worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert error.value.sent is False
    assert client.calls==[]


def test_internal_hunt_unknown_result_is_cached_even_when_hunt_command_is_unknown(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    client.failure=True
    first=worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert first['armies'][0]['status']=='unknown'
    with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
        db.execute("UPDATE hunt_commands SET status='unknown' WHERE id=?",(command_id,))
    before=len(client.calls)
    assert worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)==first
    assert len(client.calls)==before


def test_internal_hunt_detects_payload_change_during_snapshot_before_journaling(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    snapshot=client.snapshot
    def tamper_during_read(army_ids=None):
        result=snapshot(army_ids=army_ids)
        with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
            payload=json.loads(db.execute('SELECT payload FROM hunt_commands WHERE id=?',(command_id,)).fetchone()[0])
            payload['desired_total']=9000
            db.execute('UPDATE hunt_commands SET payload=? WHERE id=?',(json.dumps(payload),command_id))
        return result
    client.snapshot=tamper_during_read
    with pytest.raises(RuntimeError,match='hunt_replenish_ownership_unverified'):
        worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert [c[0] for c in client.calls]==['snapshot']
    assert journal_rows(tmp_path)==([],[])


def test_internal_hunt_accepts_old_resolved_journal_phase_only_with_fresh_idle_snapshot(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
        state=json.loads(db.execute('SELECT state FROM hunt_armies WHERE hunt_id=?',(hunt_id,)).fetchone()[0])
        state['phase']='resolved'
        db.execute('UPDATE hunt_armies SET state=? WHERE hunt_id=?',(json.dumps(state),hunt_id))
    assert worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)['armies'][0]['status']=='applied'


def test_internal_hunt_can_refill_its_idle_army_while_another_selected_army_is_fighting(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
        db.execute('INSERT INTO hunt_armies VALUES(?,?,?)',
                   (hunt_id,'12',json.dumps({'army_id':'12','phase':'fighting','encounter_open':True,'pending_command_id':'battle'})))
        db.execute('INSERT INTO hunt_commands VALUES(?,?,?,?,?,?,?,NULL)',
                   ('battle',hunt_id,'12','attack','{}','accepted',time.time()))
    assert worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)['armies'][0]['status']=='applied'
    assert client.calls[0]==('snapshot',['11'])


def test_internal_hunt_ownership_never_bypasses_an_existing_replenishment_unknown(tmp_path):
    client=ReconcileClient([row(heroes=[
        {'id':'101','troops':2000,'capacity':5000,'recovering':False},
        {'id':'102','troops':3000,'capacity':5000,'recovering':False}])])
    client.failure=True
    assert service(tmp_path,client).run(world='w',request_id='unsettled-refill')['armies'][0]['status']=='unknown'
    client.failure=False
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path,client)
    before=len([c for c in client.calls if c[0]=='execute'])
    result=worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert result['armies'][0]['reason']=='previous_command_unknown'
    assert len([c for c in client.calls if c[0]=='execute'])==before


def test_cached_hunt_result_without_request_is_readonly_and_never_sends(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    assert worker.cached_for_hunt(hunt_id=hunt_id,command_id=command_id) is None
    assert client.calls==[]
    assert not (tmp_path/'replenish').exists()


def test_cached_hunt_result_reads_completed_request_after_pause_without_observing_client(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    original=worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    before=journal_rows(tmp_path);calls=len(client.calls)
    with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
        db.execute("UPDATE hunts SET status='paused'")
        db.execute("UPDATE hunt_commands SET status='accepted'")
    cached=worker.cached_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert cached['request_id']==original['request_id']
    assert cached['armies']==original['armies']
    assert cached['hunt_id']==hunt_id
    assert cached['hunt_command_id']==command_id
    assert journal_rows(tmp_path)==before
    assert len(client.calls)==calls


def test_cached_hunt_result_can_recover_persisted_applied_child_when_request_result_is_missing(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    original=worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    with sqlite3.connect(tmp_path/'replenish'/'commands.sqlite3') as db:
        db.execute('UPDATE requests SET result=NULL')
    before=journal_rows(tmp_path);calls=len(client.calls)
    cached=worker.cached_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert cached['armies']==original['armies']
    assert cached['source']=='persisted_command'
    assert cached['hunt_id']==hunt_id
    assert cached['hunt_command_id']==command_id
    assert journal_rows(tmp_path)==before
    assert len(client.calls)==calls


@pytest.mark.parametrize('change',['missing_request','unknown','missing_child','multiple_children','no_ack'])
def test_cached_hunt_result_does_not_infer_unfinished_or_unlinked_execution(tmp_path,change):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    original=worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    with sqlite3.connect(tmp_path/'replenish'/'commands.sqlite3') as db:
        if change=='missing_request':db.execute('DELETE FROM requests')
        if change=='missing_child':db.execute('DELETE FROM commands')
        if change=='unknown':
            db.execute('UPDATE requests SET result=NULL')
            db.execute("UPDATE commands SET status='unknown'")
        if change=='multiple_children':
            db.execute('UPDATE requests SET result=NULL')
            db.execute("INSERT INTO commands SELECT 'duplicate',request_id,world,army_id,status,payload,result,extension_resolution_id FROM commands")
        if change=='no_ack':
            db.execute('UPDATE requests SET result=NULL')
            result=json.loads(db.execute('SELECT result FROM commands').fetchone()[0]);result['army_ack']=False
            db.execute('UPDATE commands SET result=?',(json.dumps(result),))
    before=journal_rows(tmp_path);calls=len(client.calls)
    assert worker.cached_for_hunt(hunt_id=hunt_id,command_id=command_id) is None
    assert journal_rows(tmp_path)==before
    assert len(client.calls)==calls


def test_cached_hunt_unknown_is_not_a_completion_proof(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    client.failure=True
    worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    calls=len(client.calls)
    assert worker.cached_for_hunt(hunt_id=hunt_id,command_id=command_id) is None
    assert len(client.calls)==calls


def test_cached_hunt_result_rejects_changed_payload_scope_without_replay(tmp_path):
    worker,client,hunt_id,command_id=pending_hunt_replenishment(tmp_path)
    worker.run_for_hunt(hunt_id=hunt_id,command_id=command_id)
    calls=len(client.calls)
    with sqlite3.connect(tmp_path/'hunts.sqlite3') as db:
        payload=json.loads(db.execute('SELECT payload FROM hunt_commands').fetchone()[0]);payload['desired_total']=9999
        db.execute('UPDATE hunt_commands SET payload=?',(json.dumps(payload),))
    with pytest.raises(ValueError,match='request_conflict'):
        worker.cached_for_hunt(hunt_id=hunt_id,command_id=command_id)
    assert len(client.calls)==calls
