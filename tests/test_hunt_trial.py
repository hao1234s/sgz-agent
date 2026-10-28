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
    assert p['arrival_morale_verified'] is False
    assert p['arrival_morale_source'] == 'client_current_morale_minus_preview_path_estimate'
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


def field_fixture():
    config, army, target, inputs, preview = fixture()
    raw = {'army_id': 7, 'army_type': 1, 'position': [10, 10], 'hero_ids': [1, 2, 3],
           'status_codes': [3], 'army_status': 3, 'system_status': 3,
           'dispatch_build_id': 8, 'dispatch_position': [20, 20], 'dispatch_entered_at': 50,
           'at_dispatch': False, 'observed_at': 100, 'is_tying': False,
           'movement_end_time': 0, 'movement_total_time': 0,
           'command': None, 'enemy_army_id': None, 'fight_position': None, 'destination': None}
    army.update(current_base_id=None, raw_status=deepcopy(raw), return_base_id='8',
        return_base_witness={'world': 'w', 'army_id': '7', 'id': '8', 'position': [20, 20],
                            'source': 'client_lua_owned_build', 'owned': True, 'observed_at': 100})
    inputs['army'] = raw
    return config, army, target, inputs, preview


def test_field_plan_preserves_launch_and_dispatch_reference_without_claiming_return_endpoint():
    from sgz_agent.hunt_trial import build_plan
    p = build_plan(*field_fixture(), minimum_morale=0, now=101)
    assert p['position'] == [10, 10]
    assert p['dispatch_position'] == [20, 20]
    assert p['return_base_id'] == '8'
    assert p['launch_mode'] == 'stationary_field'
    assert p['auto_return_mode'] == 'game_managed_auto_return'
    assert p['return_destination_verified'] is False
    assert 'return_position' not in p


@pytest.mark.parametrize('part,key,value', [
    ('raw', 'army_id', 99), ('raw', 'status_codes', [25]), ('raw', 'army_status', 25),
    ('raw', 'system_status', None), ('raw', 'movement_total_time', 1),
    ('raw', 'command', 12), ('raw', 'enemy_army_id', 100),
    ('raw', 'fight_position', [30, 10]), ('raw', 'at_dispatch', True),
    ('raw', 'hero_ids', [4, 5, 6]), ('raw', 'observed_at', 50),
    ('inputs', 'dispatch_position', [21, 20]), ('inputs', 'status_codes', [4]),
    ('base', 'owned', False), ('base', 'id', '99'), ('base', 'world', 'other'),
    ('base', 'army_id', '99'), ('base', 'position', [21, 20]),
    ('base', 'observed_at', 50), ('base', 'source', 'unverified'),
])
def test_field_launch_requires_matching_current_stationary_and_owned_dispatch_reference(part, key, value):
    from sgz_agent.hunt_trial import build_plan
    args = list(field_fixture())
    selected = {'raw': args[1]['raw_status'], 'inputs': args[3]['army'],
                'base': args[1]['return_base_witness']}[part]
    selected[key] = value
    with pytest.raises(ValueError):
        build_plan(*args, minimum_morale=0, now=101)


def route_segment(kind, length, seconds, cost, verified=False):
    return dict(kind=kind, actual_path_len=length, seconds=seconds,
                path_verified=True, path_source='server_route_preview.path',
                morale_cost_per_cell=cost, morale_rule_verified=verified,
                morale_rule_source='verified_current_rules' if verified else 'observed_current_cost')


def test_verified_free_relocation_keeps_time_and_excludes_only_its_cells_from_morale_charge():
    from sgz_agent.hunt_trial import account_route
    result = account_route([route_segment('relocate', 80, 400, 0, True),
                            route_segment('attack', 20, 300, 0.6)], initial_morale=120)
    assert result['total_seconds'] == 700
    assert result['total_actual_path_cells'] == 100
    assert result['morale_charged_path_cells'] == 20
    assert result['morale_free_path_cells'] == 80
    assert result['estimated_morale_cost'] == 12
    assert result['estimated_arrival_morale'] == 108
    assert result['arrival_morale_verified'] is False
    assert result['arrival_morale_source'] == 'segment_path_cell_estimate'


def test_route_estimate_charges_actual_winding_path_and_each_segments_own_rule():
    from sgz_agent.hunt_trial import account_route
    result = account_route([route_segment('relocate', 50, 100, 0.2),
                            route_segment('attack', 40, 600, 0.6)], initial_morale=120)
    assert result['morale_charged_path_cells'] == 90
    assert result['estimated_morale_cost'] == 34
    assert result['estimated_arrival_morale'] == 86


@pytest.mark.parametrize('patch', [dict(kind='unknown'), dict(actual_path_len=None),
    dict(actual_path_len=-1), dict(actual_path_len=True), dict(path_verified=False),
    dict(path_source=''), dict(seconds=None), dict(seconds=-1),
    dict(morale_cost_per_cell=None), dict(morale_cost_per_cell=-1),
    dict(morale_cost_per_cell=0), dict(morale_rule_source='')])
def test_unknown_route_segment_cannot_be_counted_as_zero_cost(patch):
    from sgz_agent.hunt_trial import account_route
    segment = route_segment('relocate', 80, 400, 0.6)
    segment.update(patch)
    with pytest.raises(ValueError, match='route'):
        account_route([segment], initial_morale=120)


def test_expanded_authorized_search_scope_does_not_replace_route_cells(tmp_path):
    import json
    from sgz_agent.hunt_trial import build_plan
    args = list(fixture()); args[0].data_dir = tmp_path
    (tmp_path/'hunt-user-policy.json').write_text(json.dumps({
        'world': 'w', 'search_radius': 140, 'search_radius_authorization': 'user requested wider search'}))
    args[2].update(x=100, y=10)
    args[4].update(path_len=120)
    plan = build_plan(*args, minimum_morale=0, now=101)
    assert plan['search_radius'] == 140
    assert plan['preview']['path_len'] == 120
    assert plan['arrival_morale'] == pytest.approx(48)
    args[2]['x'] = 151
    with pytest.raises(ValueError, match='scope'):
        build_plan(*args, minimum_morale=0, now=101)


def live_path_preview():
    return dict(result=1, path_len=71, need_time=700, speed=150, need_energy=15,
        path_waypoints=[[700,1522],[679,1511],[641,1530],[631,1525],[630,1526]])


def test_native_preview_nodes_exclude_origin_from_exact_charged_cells(tmp_path):
    import json
    from sgz_agent.hunt_trial import build_plan
    args = list(fixture()); args[0].data_dir = tmp_path
    (tmp_path/'hunt-user-policy.json').write_text(json.dumps(dict(
        world='w', search_radius=140, search_radius_authorization='user')))
    args[1]['position'] = args[3]['army']['position'] = [700,1522]
    args[2].update(x=630,y=1526)
    args[4] = live_path_preview()
    result = build_plan(*args, minimum_morale=78, now=101)
    assert result['preview']['actual_path_len'] == 70
    assert result['preview']['path_length_verified'] is True
    assert result['preview']['path_length_source'] == 'cs_army_preview.path_nodes_minus_origin'
    assert result['morale_charged_path_cells'] == 70
    assert result['arrival_morale'] == 78
    assert result['arrival_morale_verified'] is False
    assert result['route_accounting']['total_seconds'] == 700
    assert result['route_accounting']['estimated_morale_cost'] == 42
    assert result['preflight_morale'] == 120


@pytest.mark.parametrize('patch', [dict(path_len=0), dict(path_len=1), dict(path_len=2),
    dict(path_len=70), dict(path_len=True),
    dict(path_waypoints=None), dict(path_waypoints=[]),
    dict(path_waypoints=[[701,1522],[630,1526]]),
    dict(path_waypoints=[[700,1522],[631,1526]]),
    dict(path_waypoints=[[700,1522],[0,1],[630,1526]]),
    dict(actual_path_len=69), dict(path_length_verified=False),
    dict(path_length_source='coordinate_distance')])
def test_native_path_proof_rejects_malformed_or_forged_route_cells(patch):
    from sgz_agent.hunt_client import normalize_route_preview
    raw = live_path_preview(); raw.update(patch)
    with pytest.raises(ValueError, match='preview_path'):
        normalize_route_preview(raw, origin=[700,1522], target=[630,1526])


def test_legacy_preview_cannot_claim_exact_cells_without_native_waypoints():
    from sgz_agent.hunt_client import normalize_route_preview
    raw = live_path_preview(); raw.pop('path_waypoints')
    raw.update(actual_path_len=70, path_length_verified=True,
               path_length_source='cs_army_preview.path_nodes_minus_origin')
    with pytest.raises(ValueError, match='preview_path'):
        normalize_route_preview(raw, origin=[700,1522], target=[630,1526])
