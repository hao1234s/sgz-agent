"""Controlled native entry cannot widen discovery beyond world-bound policy."""
import json
from types import SimpleNamespace

import pytest


def attack_plan(radius=None):
    value={'world':'w','army_id':'123','min_morale':100,
           'target':{'id':'456','x':600,'y':1500,'use_code':1,'conf_id':10}}
    if radius is not None:value['search_radius']=radius
    return value


@pytest.mark.parametrize('radius,policy',[
    (140,None),
    (140,{'world':'other','search_radius':140,'search_radius_authorization':'explicit user instruction'}),
    (141,{'world':'w','search_radius':140,'search_radius_authorization':'explicit user instruction'}),
    ('140',None),(True,None)])
def test_controlled_radius_bypass_rejected_before_parent_runtime_or_attach(tmp_path,monkeypatch,radius,policy):
    from sgz_agent import hunt_attack as m
    entered=[]
    monkeypatch.setattr(m.HuntNativeRuntime,'__init__',lambda *a,**k:entered.append('parent_runtime'))
    config=SimpleNamespace(data_dir=tmp_path,identity={'world':'w'})
    if policy:
        (tmp_path/'hunt-user-policy.json').write_text(json.dumps({
            'min_arrival_morale':100,'authorization':'existing morale policy',**policy}),encoding='utf-8')
    with pytest.raises(ValueError,match='attack_search_policy_not_authorized'):
        m.ControlledAttackRuntime(config,plan=attack_plan(radius),controlled_verification=True)
    assert entered==[]


@pytest.mark.parametrize('radius,authorized',[(None,70),(70,70),(70,140),(140,140)])
def test_controlled_radius_allows_legacy_default_or_explicit_world_authorization(tmp_path,monkeypatch,radius,authorized):
    from sgz_agent import hunt_attack as m
    entered=[]
    monkeypatch.setattr(m.HuntNativeRuntime,'__init__',lambda *a,**k:entered.append(k))
    config=SimpleNamespace(data_dir=tmp_path,identity={'world':'w'})
    if authorized==140:
        (tmp_path/'hunt-user-policy.json').write_text(json.dumps({
            'world':'w','min_arrival_morale':100,'authorization':'existing morale policy',
            'search_radius':140,'search_radius_authorization':'explicit user instruction'}),encoding='utf-8')
    runtime=m.ControlledAttackRuntime(config,plan=attack_plan(radius),controlled_verification=True)
    assert runtime.attack_plan==attack_plan(radius)
    assert entered==[{'enabled':True,'controlled_verification':True}]
