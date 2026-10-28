"""Only the current authorized ordinary level-nine or level-ten target may send."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


PROFILE9 = dict(use_code=1, conf_id=9, display_name='重徒兵', defender_count=2)
PROFILE10 = dict(use_code=1, conf_id=10, display_name='巨戟士', defender_count=3)


def authorize(config, conf=9):
    (config.data_dir/'hunt-user-policy.json').write_text(json.dumps(dict(world='w',
        min_arrival_morale=0, authorization='user', target_conf_id=conf,
        target_authorization='user requested target change')), encoding='utf-8')


def test_authorized_level_nine_plan_uses_two_fresh_config_defenders(tmp_path):
    from test_hunt_trial import fixture
    from sgz_agent.hunt_trial import build_plan
    args = list(fixture()); args[0].data_dir = tmp_path; authorize(args[0])
    args[2]['conf_id'] = 9
    args[3].update(defender_count=2, target_level=9)
    result = build_plan(*args, minimum_morale=0, now=101)
    assert result['target_profile'] == PROFILE9
    assert result['target']['conf_id'] == 9
    assert result['defenders']['count'] == 2
    for patch in (dict(defender_count=3), dict(target_level=10)):
        wrong = deepcopy(args); wrong[3].update(patch)
        with pytest.raises(ValueError):
            build_plan(*wrong, minimum_morale=0, now=101)
    args[2]['conf_id'] = 10
    with pytest.raises(ValueError):
        build_plan(*args, minimum_morale=0, now=101)


def test_level_nine_attack_request_requires_matching_explicit_profile():
    from test_hunt_attack import plan
    from sgz_agent.hunt_attack import attack_request
    p = plan(); p['target']['conf_id'] = 9; p['target_profile'] = deepcopy(PROFILE9)
    assert attack_request(p)['cmd'] == 12
    assert attack_request(p)['is_auto_fallback'] is True
    for profile in (None, PROFILE10, {**PROFILE9, 'use_code': 3}, {**PROFILE9, 'defender_count': 3}):
        wrong = deepcopy(p)
        if profile is None: wrong.pop('target_profile')
        else: wrong['target_profile'] = profile
        with pytest.raises(ValueError): attack_request(wrong)
    p['target']['use_code'] = 3
    with pytest.raises(ValueError): attack_request(p)


def test_level_nine_runtime_is_bound_to_current_world_policy(tmp_path):
    from test_hunt_attack import plan
    from sgz_agent.hunt_attack import ControlledAttackRuntime
    config = SimpleNamespace(data_dir=tmp_path, identity={'world':'w'}, executable=Path('S3Client.exe'))
    p = plan(); p['target']['conf_id'] = 9; p['target_profile'] = deepcopy(PROFILE9)
    with pytest.raises(ValueError, match='target_profile'):
        ControlledAttackRuntime(config, plan=p, controlled_verification=True)
    authorize(config)
    runtime = ControlledAttackRuntime(config, plan=p, controlled_verification=True)
    assert runtime.attack_plan['target_profile'] == PROFILE9
    p['target']['conf_id'] = 10; p['target_profile'] = deepcopy(PROFILE10)
    with pytest.raises(ValueError, match='target_profile'):
        ControlledAttackRuntime(config, plan=p, controlled_verification=True)


def test_send_guard_requires_level_nine_classification_and_exact_fresh_config():
    from test_hunt_attack import plan, snapshot
    from sgz_agent import hunt_attack
    source = Path(hunt_attack.__file__).with_suffix('.js').read_text(encoding='utf-8').split('// Native transport')[0]
    p = plan(); p['target']['conf_id'] = 9
    p.update(target_profile=deepcopy(PROFILE9), defenders={'count':2})
    row = snapshot(); row.update(defender_count=2,target_level=9,cost_energy=15,can_auto_return=True)
    valid = dict(plan=p,row=row,target=p['target'],now=101); cases=[valid]
    for part, patch in [('row',dict(defender_count=3)),('row',dict(target_level=10)),
        ('row',dict(cost_energy=20)),('row',dict(can_auto_return=False)),
        ('target',dict(use_code=3)),('target',dict(conf_id=10)),
        ('profile',dict(conf_id=10)),('profile',dict(defender_count=3))]:
        wrong=deepcopy(valid)
        (wrong['plan']['target_profile'] if part=='profile' else wrong[part]).update(patch)
        cases.append(wrong)
    result=subprocess.run(['node','-'],input=source+';console.log(JSON.stringify('+json.dumps(cases)+
        '.map(x=>validateAttack(x.plan,x.row,x.target,x.now))));',capture_output=True,text=True,check=True)
    actual=json.loads(result.stdout)
    assert actual[0] is None
    assert all(isinstance(value,str) and value for value in actual[1:])


def test_level_nine_victory_requires_two_matching_wins_and_never_promotes_unknown(tmp_path):
    from test_hunt_attack import plan
    from sgz_agent.hunt_attack import AttackJournal
    p=plan();p['target']['conf_id']=9;p['target_profile']=deepcopy(PROFILE9)
    with AttackJournal(tmp_path) as store:
        record=store.prepare('nine','w',p);store.claim(record['id'])
        store.finish(record['id'],dict(status='accepted',sent=True,departure_verified=True))
        start=store.get(record['id'])['claimed_at']
        proof=dict(entity_id='2511112',monster_id=123,x=587,y=1550,use_code=1,conf_id=9,observed_at=100)
        reports=[dict(id=i,group_id='123',pos=[587,1550],player_id=42,result=1,
            hero_ids=[1,2,3],primary_hero_id=1,battle_time=start+300) for i in (1001,1002)]
        post=dict(army_id=364067,status_codes=[19],position=[558,1527],dispatch_build_id=11183,
            movement_total_time=0,movement_end_time=0,observed_at=start+310)
        for wrong_reports in (reports[:1],reports+[dict(reports[0],id=1003)]):
            with pytest.raises(ValueError):
                store.record_victory(record['id'],target_witness=proof,reports=wrong_reports,
                                     role_id=42,post_status=post,now=start+311)
        with pytest.raises(ValueError):
            store.record_victory(record['id'],target_witness={**proof,'conf_id':10},reports=reports,
                                 role_id=42,post_status=post,now=start+311)
        result=store.record_victory(record['id'],target_witness=proof,reports=reports,
                                    role_id=42,post_status=post,now=start+311)
        assert result['completed_waves']==2 and result['encounter_complete'] is True
        assert len(store.pending('w'))==1
    with AttackJournal(tmp_path/'unknown') as store:
        unknown=store.prepare('unknown-nine','w',p);store.claim(unknown['id'])
        store.finish(unknown['id'],dict(status='unknown',sent=True))
        with pytest.raises(ValueError):
            store.record_victory(unknown['id'],target_witness=proof,reports=reports,
                                 role_id=42,post_status=post,now=start+311)
        assert store.get(unknown['id'])['status']=='unknown'
        assert store.get(unknown['id'])['result']==dict(status='unknown',sent=True)
