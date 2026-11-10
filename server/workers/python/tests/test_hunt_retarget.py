"""Explicit retargeting releases only inactive army claims, never old targets."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import sqlite3
from types import SimpleNamespace

import pytest

from sgz_agent.hunt_attack import AttackJournal


IDENTITY={'world':'w','role_id':'42','region':'r','api_server_id':'s','api_season':1}
AUTH={'world':'w','target_conf_id':9,'statement':'User explicitly switched all ready armies to target 9'}


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def setup(tmp_path,monkeypatch,*,field=False,conf_id=10):
    from sgz_agent import hunt_attack
    monkeypatch.setattr(hunt_attack.time,'time',lambda:1000)
    journal=AttackJournal(tmp_path)
    p={'world':'w','army_id':'123','base_id':'101','position':[600,1500],
       'heroes':[{'id':str(i)} for i in (1,2,3)],
       'target':{'id':'456','x':650,'y':1550,'use_code':1,'conf_id':conf_id,'end_time':5000},
       'created_at':999,'preview':{'need_time':300}}
    if conf_id==9:p['target_profile']={'use_code':1,'conf_id':9,'display_name':'重徒兵','defender_count':2}
    if field:p.update(launch_mode='stationary_field',position=[605,1495],dispatch_position=[625,1477],return_destination_verified=False)
    request='old-round';command='dispatch:'+hashlib.sha256(encode([request,'123']).encode()).hexdigest()
    record=journal.prepare(command,'w',p);journal.claim(record['id'])
    journal.finish(record['id'],{'status':'accepted','sent':True,'departure_verified':True,
        'encounter_complete':False,'events':[{'identity':deepcopy(IDENTITY)}]})
    record=journal.get(record['id']);position=p.get('dispatch_position',p['position'])
    pair=[]
    for n,t in enumerate((1016,1018)):
        row={'army_id':123,'army_type':1,'hero_ids':[1,2,3],'status_codes':[19],
            'army_status':19,'system_status':19,'is_tying':True,'position':position,
            'dispatch_position':position,'dispatch_build_id':101,'at_dispatch':True,
            'dispatch_entered_at':900,'movement_end_time':0,'movement_total_time':0,
            'command':None,'destination':None,'enemy_army_id':None,'fight_position':None,'observed_at':t}
        pair.append({'identity':deepcopy(IDENTITY),'client_session':{'pid':10,'created_at':800},
            'runtime_final':{'generation':'roster-'+str(n),'phase':'closed','fault':None,'cleanup_fault':None,'bridge_retained':False},
            'roster':{'complete':True,'observed_at':t,'armies':[row]}})
    bases={'identity':deepcopy(IDENTITY),'client_session':{'pid':10,'created_at':800},
        'runtime_final':{'generation':'owned-bases','phase':'closed','fault':None,'cleanup_fault':None,'bridge_retained':False},
        'bases':{'observed_at':1019,'bases':[{'id':'101','position':position,
            'source':'client_lua_owned_build','creating':False,'destroying':False}]}}
    spawn=encode([650,1550,1,conf_id,5000.0]);formation='heroes:1,2,3'
    with sqlite3.connect(tmp_path/'hunt-dispatches.sqlite3') as db:
        db.executescript('CREATE TABLE dispatch_batches(request_id TEXT PRIMARY KEY,world TEXT,selection TEXT,snapshot TEXT);'
                        'CREATE TABLE dispatch_reservations(world TEXT,kind TEXT,resource TEXT,request_id TEXT,army_id TEXT,PRIMARY KEY(world,kind,resource));')
        snapshot={'armies':[{'army_id':'123','status':'sent','target':p['target'],'receipt':{'command_id':record['id']}}]}
        db.execute('INSERT INTO dispatch_batches VALUES(?,?,?,?)',(request,'w','all',encode(snapshot)))
        db.executemany('INSERT INTO dispatch_reservations VALUES(?,?,?,?,?)',
            [('w',k,v,request,'123') for k,v in [('army','123'),('formation',formation),('target','456'),('spawn',spawn)]])
    return journal,record,pair,bases


def retire(journal,record,pair,bases=None,**kwargs):
    assert importlib.util.find_spec('sgz_agent.hunt_retarget') is not None,'retarget reconciliation is not implemented'
    from sgz_agent.hunt_retarget import reconcile_returned_army_for_retarget
    return reconcile_returned_army_for_retarget(journal,record['id'],identity=IDENTITY,
        authorization=kwargs.get('authorization',AUTH),witnesses=pair,owned_bases=bases,now=kwargs.get('now',1020))


@pytest.mark.parametrize('field',[False,True])
def test_retarget_preserves_receipt_and_old_targets_but_releases_only_army_claims(tmp_path,monkeypatch,field):
    journal,record,pair,bases=setup(tmp_path,monkeypatch,field=field)
    with journal:
        original=journal.db.execute('SELECT result FROM attacks').fetchone()[0]
        evidence=retire(journal,record,pair,bases if field else None)
        assert journal.get(record['id'])['status']=='retired_returned'
        assert journal.db.execute('SELECT result FROM attacks').fetchone()[0]==original
        assert evidence['historical_outcome']=='unknown' and evidence['readiness_revalidated'] is False
        assert journal.pending('w')==[] and journal.claim(record['id']) is False
        assert journal.db.execute('SELECT target_id FROM retained_attack_targets').fetchone()[0]=='456'
        with sqlite3.connect(tmp_path/'hunt-dispatches.sqlite3') as db:
            assert {r[0] for r in db.execute('SELECT kind FROM dispatch_reservations')}=={'target','spawn'}
            assert json.loads(db.execute('SELECT snapshot FROM dispatch_batches').fetchone()[0])['armies'][0]['status']=='sent'
        assert retire(journal,record,pair,bases if field else None)==evidence
        with pytest.raises(ValueError,match='retarget_evidence_conflict'):
            retire(journal,record,pair,bases if field else None,authorization={**AUTH,'statement':'different'})


@pytest.mark.parametrize('mutation',[
    'stale','same_generation','same_time','different_process','incomplete','wrong_identity',
    'wrong_hero_order','duplicate_hero','duplicate_army','moving','fighting','draw','command',
    'enemy','fight_position','destination','wrong_base','wrong_position','not_at_base','open_runtime',
    'runtime_fault','missing_command','not_accepted','not_departed','wrong_target','wrong_authorization'])
def test_retarget_fails_closed_without_full_same_force_inactivity_and_authorization(tmp_path,monkeypatch,mutation):
    journal,record,pair,bases=setup(tmp_path,monkeypatch);row=pair[1]['roster']['armies'][0];auth=deepcopy(AUTH)
    if mutation=='stale':pair[0]['roster']['observed_at']=980
    elif mutation=='same_generation':pair[1]['runtime_final']['generation']='roster-0'
    elif mutation=='same_time':pair[1]['roster']['observed_at']=1016;row['observed_at']=1016
    elif mutation=='different_process':pair[1]['client_session']['pid']=11
    elif mutation=='incomplete':pair[1]['roster']['complete']=False
    elif mutation=='wrong_identity':pair[1]['identity']['role_id']='43'
    elif mutation=='wrong_hero_order':row['hero_ids']=[2,1,3]
    elif mutation=='duplicate_hero':pair[1]['roster']['armies'].append({**deepcopy(row),'army_id':124})
    elif mutation=='duplicate_army':pair[1]['roster']['armies'].append(deepcopy(row))
    elif mutation=='moving':row['movement_end_time']=1050
    elif mutation in ('fighting','draw'):row.update(status_codes=[24 if mutation=='fighting' else 25],army_status=24,system_status=24)
    elif mutation=='command':row['command']=12
    elif mutation=='enemy':row['enemy_army_id']=55
    elif mutation=='fight_position':row['fight_position']=[650,1550]
    elif mutation=='destination':row['destination']=[650,1550]
    elif mutation=='wrong_base':row['dispatch_build_id']=102
    elif mutation=='wrong_position':row['position']=[601,1500]
    elif mutation=='not_at_base':row['at_dispatch']=False
    elif mutation=='open_runtime':pair[1]['runtime_final']['phase']='running'
    elif mutation=='runtime_fault':pair[1]['runtime_final']['fault']='bad'
    elif mutation=='missing_command':row.pop('command')
    elif mutation in ('not_accepted','not_departed'):
        result=deepcopy(record['result']);result['departure_verified']=mutation!='not_departed'
        journal.db.execute('UPDATE attacks SET status=?,result=?',('unknown' if mutation=='not_accepted' else 'accepted',encode(result)));journal.db.commit()
    elif mutation=='wrong_target':
        plan=deepcopy(record['plan']);plan['target']['conf_id']=9
        journal.db.execute('UPDATE attacks SET plan=?',(encode(plan),));journal.db.commit()
    elif mutation=='wrong_authorization':auth['world']='other'
    with journal:
        with pytest.raises(ValueError):retire(journal,record,pair,authorization=auth)
        assert journal.db.execute('SELECT status FROM attacks').fetchone()[0] in ('accepted','unknown')
        with sqlite3.connect(tmp_path/'hunt-dispatches.sqlite3') as db:
            assert db.execute('SELECT count(*) FROM dispatch_reservations').fetchone()[0]==4


@pytest.mark.parametrize('mutation',['missing','foreign_base','wrong_position','unclean','wrong_session'])
def test_field_retarget_requires_current_owned_base_read(tmp_path,monkeypatch,mutation):
    journal,record,pair,bases=setup(tmp_path,monkeypatch,field=True)
    if mutation=='missing':bases=None
    elif mutation=='foreign_base':bases['bases']['bases'][0]['source']='client_aoi'
    elif mutation=='wrong_position':bases['bases']['bases'][0]['position']=[605,1495]
    elif mutation=='unclean':bases['runtime_final']['fault']='bad'
    elif mutation=='wrong_session':bases['client_session']['pid']=11
    with journal:
        with pytest.raises(ValueError):retire(journal,record,pair,bases)
        assert journal.get(record['id'])['status']=='accepted'


def test_attack_prepare_retained_target_and_spawn_fences(tmp_path):
    with AttackJournal(tmp_path) as journal:
        assert journal.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='retained_attack_targets'").fetchone(), 'retained target fence is not implemented'
        target={'id':'456','x':650,'y':1550,'use_code':1,'conf_id':10,'end_time':5000}
        spawn=encode([650,1550,1,10,5000.0])
        journal.db.execute('INSERT INTO retained_attack_targets VALUES(?,?,?,?)',('w','456','old',spawn));journal.db.commit()
        for identity in ['456','789']:
            p={'world':'w','army_id':'123','target':{**target,'id':identity}}
            with pytest.raises(RuntimeError,match='attack_target_retained'):journal.prepare('new-'+identity,'w',p)


def test_attack_retirement_crash_leaves_batch_army_fenced_and_same_proof_can_finish(tmp_path,monkeypatch):
    from sgz_agent import hunt_retarget
    journal,record,pair,_bases=setup(tmp_path,monkeypatch)
    release=hunt_retarget._release_army
    def crash(*args):raise RuntimeError('simulated_between_databases')
    monkeypatch.setattr(hunt_retarget,'_release_army',crash)
    with journal:
        original=journal.db.execute('SELECT result FROM attacks').fetchone()[0]
        with pytest.raises(RuntimeError,match='simulated_between_databases'):retire(journal,record,pair)
        assert journal.get(record['id'])['status']=='retired_returned'
        assert journal.db.execute('SELECT count(*) FROM retained_attack_targets').fetchone()[0]==1
        assert journal.db.execute('SELECT result FROM attacks').fetchone()[0]==original
        with sqlite3.connect(tmp_path/'hunt-dispatches.sqlite3') as db:
            assert db.execute('SELECT count(*) FROM dispatch_reservations').fetchone()[0]==4
        monkeypatch.setattr(hunt_retarget,'_release_army',release)
        retire(journal,record,pair)
        with sqlite3.connect(tmp_path/'hunt-dispatches.sqlite3') as db:
            assert {r[0] for r in db.execute('SELECT kind FROM dispatch_reservations')}=={'target','spawn'}


def continue_round(journal,record,pair,**changes):
    from sgz_agent.hunt_retarget import reconcile_returned_army_for_next_round
    auth={**AUTH,'action':'continue_round','request_id':'new-heavy-round',
          'statement':'User explicitly says continue attacking ordinary heavy infantry'}
    auth.update(changes.pop('auth',{}))
    return reconcile_returned_army_for_next_round(journal,record['id'],identity=IDENTITY,
        authorization=auth,witnesses=pair,now=1020,**changes)


def test_explicit_heavy_continuation_releases_returned_force_and_retains_old_target(tmp_path,monkeypatch):
    journal,record,pair,_=setup(tmp_path,monkeypatch,conf_id=9)
    with journal:
        original=journal.db.execute('SELECT result FROM attacks').fetchone()[0]
        evidence=continue_round(journal,record,pair)
        assert evidence['terminal_basis']=='two_clean_current_inactive_rosters_explicit_next_round'
        assert evidence['historical_outcome']=='unknown'
        assert journal.get(record['id'])['status']=='retired_returned'
        assert journal.db.execute('SELECT result FROM attacks').fetchone()[0]==original
        assert continue_round(journal,record,pair)==evidence
        with sqlite3.connect(tmp_path/'hunt-dispatches.sqlite3') as db:
            assert {r[0] for r in db.execute('SELECT kind FROM dispatch_reservations')}=={'target','spawn'}
        p=deepcopy(record['plan']);p['target']['id']='789'
        with pytest.raises(RuntimeError,match='attack_target_retained'):journal.prepare('other','w',p)
        p['target'].update(x=660,y=1560)
        assert journal.prepare('next','w',p)['status']=='prepared'


@pytest.mark.parametrize('problem',['missing_action','missing_request','wrong_old_profile','moving','unknown','not_departed','stale','same_generation'])
def test_continuation_requires_explicit_new_round_and_existing_full_inactivity_proof(tmp_path,monkeypatch,problem):
    journal,record,pair,_=setup(tmp_path,monkeypatch,conf_id=10 if problem=='wrong_old_profile' else 9)
    auth={}
    if problem=='missing_action':auth['action']=None
    elif problem=='missing_request':auth['request_id']=''
    elif problem=='moving':pair[1]['roster']['armies'][0]['movement_end_time']=1050
    elif problem=='stale':pair[0]['roster']['observed_at']=980
    elif problem=='same_generation':pair[1]['runtime_final']['generation']='roster-0'
    elif problem=='unknown':journal.db.execute("UPDATE attacks SET status='unknown'");journal.db.commit()
    elif problem=='not_departed':
        receipt=deepcopy(record['result']);receipt['departure_verified']=False
        journal.db.execute('UPDATE attacks SET result=?',(encode(receipt),));journal.db.commit()
    with journal:
        with pytest.raises(ValueError):continue_round(journal,record,pair,auth=auth)
        assert len(journal.pending('w'))==1
        with sqlite3.connect(tmp_path/'hunt-dispatches.sqlite3') as db:
            assert db.execute('SELECT count(*) FROM dispatch_reservations').fetchone()[0]==4
