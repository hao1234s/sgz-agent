from copy import deepcopy
from types import SimpleNamespace
import pytest


def fixture():
    config=SimpleNamespace(identity={'world':'w'})
    army={'id':'7','kind':'regular','formed':True,'phase':'stationed','position':[10,10],'current_base_id':'8'}
    target={'id':'9','x':30,'y':10,'use_code':1,'conf_id':10,'army_conf_id':30075,
            'troop_type':2,'end_time':1000,'observed_at':100,'source':'server_aoi_refresh'}
    inputs={'army':{'army_id':7,'dispatch_build_id':8,'position':[10,10]},
            'heroes':[{'id':str(i),'is_main':i==1,'troops':9000,'capacity':10000,
                       'stamina':120,'wounded_until':0} for i in [1,2,3]],
            'morale':120,'stamina':120,'morale_cost_per_cell':0.6,'observed_at':100,
            'orders_available':48,'cost_decree':1,'cost_energy':15,'can_auto_return':True,
            'defender_count':3,'target_level':10}
    preview={'result':1,'need_time':1005,'path_len':68,'need_energy':15}
    return config,army,target,inputs,preview


def test_trial_plan_uses_authorized_floor_and_marks_unknown_defender_strength():
    from sgz_agent.hunt_trial import build_plan
    args=fixture()
    p=build_plan(*args,minimum_morale=0,now=101)
    assert p['arrival_morale']==pytest.approx(79.2)
    assert p['min_morale']==0 and p['base_id']=='8'
    assert p['defenders']['count']==3 and p['defenders']['troops_each'] is None
    assert p['assessment']['win_probability'] is None
    with pytest.raises(ValueError,match='morale'):
        build_plan(*args,minimum_morale=100,now=101)


@pytest.mark.parametrize('index,key,value',[(1,'phase','marching'),(1,'current_base_id',None),
    (2,'use_code',3),(2,'observed_at',50),(2,'end_time',100),
    (3,'orders_available',0),(3,'cost_energy',20),(3,'defender_count',None),
    (4,'result',False),(4,'need_energy',None)])
def test_trial_refuses_missing_current_evidence(index,key,value):
    from sgz_agent.hunt_trial import build_plan
    args=list(fixture());args[index][key]=value
    with pytest.raises(ValueError):build_plan(*args,minimum_morale=0,now=101)
