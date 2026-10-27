from copy import deepcopy

import pytest

from sgz_agent.hunt import HuntCoordinator, choose_route
from sgz_agent.hunt_store import HuntStore


def army(identity='a', **extra):
    return dict(id=identity, kind='regular', home_city_id='home', phase='idle',
                position=[500, 500], encounter_complete=False, result=None,
                deployment_base_id=None, current_base_id='home', **extra)


class Client:
    def __init__(self):
        self.statuses = {'a': army(), 'b': army('b')}
        self.ready = {key: dict(**value, heroes=[dict(id=f'{key}-{i}', is_main=i == 0,
                      troops=10000, capacity=10000) for i in range(3)], morale=100,
                      stamina_sufficient=True, orders_sufficient=True, ready=True,
                      recruitment_state_verified=True, recruitment_active=False,
                      wounded_recovery_pending=False)
                      for key, value in self.statuses.items()}
        self.locations = [dict(id='home', kind='main', position=[500, 500], available=True,
                          can_recruit=True, capacity_available=True, eligible_army_ids=['a','b']),
                          dict(id='tent', kind='camp', position=[530, 500], available=True,
                          can_recruit=True, capacity_available=True, eligible_army_ids=['a','b'])]
        self.targets = [dict(id='g1', x=550, y=500, use_code=1, conf_id=10),
                        dict(id='g2', x=560, y=500, use_code=1, conf_id=10)]
        self.calls = []
        self.routes = [dict(kind='direct', base_id=None, travel_seconds=0, prepare_seconds=0,
                            attack_seconds=600, arrival_morale=100, verified=True)]
        self.result = {'status':'accepted'}
        self.reconciled = 'unknown'
        self.complete = True

    def capabilities(self): return {'available':True,'missing':[]}
    def roster_status(self):
        self.calls.append(('status',))
        return dict(world='w', complete=self.complete, armies=deepcopy(list(self.statuses.values())))
    def ready_army(self, identity):
        self.calls.append(('ready',identity))
        assert self.statuses[identity]['phase'] in {'idle','stationed','resolved'}
        return {**deepcopy(self.ready[identity]), **deepcopy(self.statuses[identity])}
    def bases(self): return deepcopy(self.locations)
    def search_giants(self, center, radius, *, army_id=None):
        self.calls.append(('scan', center, radius))
        return dict(complete=True, targets=deepcopy(self.targets))
    def route_options(self, a, target, bases):
        return [dict(**r,target_id=target['id']) for r in self.routes]
    def execute(self, command):
        self.calls.append(('execute',deepcopy(command)))
        if isinstance(self.result, Exception): raise self.result
        return self.result
    def reconcile(self, command, status):
        self.calls.append(('reconcile',command['id']))
        return self.reconciled


@pytest.fixture
def setup(tmp_path):
    store, client = HuntStore(tmp_path), Client()
    coordinator = HuntCoordinator(store, client, world='w')
    yield coordinator, store, client
    coordinator.close()
    store.close()


def begin(setup, **kwargs):
    coordinator, store, client = setup
    campaign = coordinator.start(request_id='request', now=1000, **kwargs)
    return coordinator, store, client, campaign['id']


def sent(client):
    return [row[1] for row in client.calls if row[0] == 'execute']


def test_reconcile_retains_the_original_command_receipt(setup):
    coordinator, store, client, identity = begin(setup, army_ids=['a'])
    client.result = {'status': 'accepted', 'sent': True, 'witness': {'native_calls': 1}}
    coordinator.tick(now=1000)
    command = sent(client)[0]
    client.reconciled = 'applied'
    client.statuses['a']['phase'] = 'marching'
    coordinator.tick(now=1120)
    assert store.command(command['id'])['result'] == client.result


def test_adapter_verified_applied_result_is_not_downgraded_to_unknown(setup):
    coordinator, store, client, identity = begin(setup, army_ids=['a'])
    client.result = {'status': 'applied', 'sent': True, 'verified_full': True}
    coordinator.tick(now=1000)
    assert store.command(sent(client)[0]['id'])['status'] == 'applied'


def test_unavailable_resume_leaves_campaign_paused_and_blocked(setup):
    coordinator, store, client, identity = begin(setup, army_ids=['a'])
    coordinator.stop(identity)
    state = store.get(identity)['armies'][0]
    state['blocked'] = True
    store.save_army(identity, state)
    client.capabilities = lambda: {'available': False, 'missing': ['attack']}
    with pytest.raises(RuntimeError, match='hunt_capabilities_missing:attack'):
        coordinator.resume(identity, now=1200)
    assert store.get(identity)['status'] == 'paused'
    assert store.get(identity)['armies'][0]['blocked'] is True


def test_verified_unsent_resource_wait_does_not_require_manual_resume(setup):
    coordinator, store, client, identity = begin(setup, army_ids=['a'])
    client.ready['a']['heroes'][0]['troops']=5000
    client.result={'status':'not_applied','sent':False,'retryable':True,'reason':'reserve_insufficient'}
    coordinator.tick(now=1000)
    coordinator.tick(now=1120)
    state=store.get(identity)['armies'][0]
    assert state['blocked'] is False
    assert len(sent(client))==1
    assert state['reason']=='reserve_insufficient'
    coordinator.tick(now=1240)
    assert len(sent(client))==2


def test_two_teams_reserve_distinct_targets_and_poll_every_120_seconds(setup):
    coordinator, store, client, identity = begin(setup)
    coordinator.tick(now=1000)
    commands = sent(client)
    assert {c['army_id'] for c in commands} == {'a','b'}
    assert len({c['payload']['target']['id'] for c in commands}) == 2
    assert all(c['kind'] == 'attack' for c in commands)
    assert all(row[2] == 70 for row in client.calls if row[0] == 'scan')
    before = len(client.calls)
    coordinator.tick(now=1119)
    assert len(client.calls) == before
    coordinator.tick(now=1120)
    assert len(sent(client)) == 2
    assert store.get(identity)['next_poll_at'] == 1240


def test_all_armies_excludes_auxiliary_and_requires_complete_roster(setup):
    coordinator, store, client = setup
    client.statuses['aux'] = {**army('aux'),'kind':'auxiliary'}
    result = coordinator.start(request_id='all', now=1000)
    assert {s['army_id'] for s in result['armies']} == {'a','b'}
    coordinator.stop(result['id'])
    client.complete = False
    with pytest.raises(RuntimeError, match='roster_incomplete'):
        coordinator.start(request_id='new-all',now=1001)


@pytest.mark.parametrize('phase', ['marching','fighting','draw','unknown','returning','recruiting','relocating'])
def test_active_team_has_no_troop_or_map_query_while_other_team_proceeds(setup, phase):
    coordinator, store, client = setup
    client.statuses['a']['phase'] = phase
    coordinator.start(request_id='all',now=1000)
    coordinator.tick(now=1000)
    assert ('ready','a') not in client.calls
    assert ('ready','b') in client.calls
    assert [c['army_id'] for c in sent(client)] == ['b']


def test_partial_battle_result_or_idle_alone_does_not_end_encounter(setup):
    coordinator, store, client, identity = begin(setup,army_ids=['a'])
    coordinator.tick(now=1000)
    client.reconciled = 'applied'
    client.calls.clear()
    client.statuses['a'].update(phase='idle',result='victory',encounter_complete=False)
    coordinator.tick(now=1120)
    assert ('ready','a') not in client.calls
    assert not sent(client)
    assert store.get(identity)['armies'][0]['encounter_open'] is True


def test_finished_team_returns_to_deployment_base_before_home(setup):
    coordinator, store, client, identity = begin(setup,army_ids=['a'])
    coordinator.tick(now=1000)
    target = sent(client)[0]['payload']['target']['id']
    client.reconciled = 'applied'
    client.statuses['a'].update(phase='idle',current_base_id=None,deployment_base_id='tent',
                              encounter_complete=True,completed_target_id=target,result='victory')
    for hero in client.ready['a']['heroes']: hero['troops'] = 6000
    coordinator.tick(now=1120)
    command = sent(client)[-1]
    assert command['kind'] == 'return' and command['payload']['base_id'] == 'tent'


def test_tent_recruits_only_when_short_and_waits_for_completion(setup):
    coordinator, store, client = setup
    client.statuses['a'].update(current_base_id='tent',deployment_base_id='tent')
    for h in client.ready['a']['heroes']: h['troops'] = 5000
    coordinator.start(request_id='all',now=1000,army_ids=['a'])
    coordinator.tick(now=1000)
    assert sent(client)[-1]['kind'] == 'recruit'
    assert sent(client)[-1]['payload']['desired_total'] == 30000
    client.statuses['a']['phase'] = 'recruiting'
    client.reconciled = 'applied'
    coordinator.tick(now=1120)
    assert len(sent(client)) == 1


def test_city_refills_even_if_troops_above_departure_threshold(setup):
    coordinator, store, client = setup
    for h in client.ready['a']['heroes']: h['troops'] = 9000
    coordinator.start(request_id='all',now=1000,army_ids=['a'])
    coordinator.tick(now=1000)
    command = sent(client)[0]
    assert command['kind'] == 'replenish'
    assert command['payload']['base_id'] == 'home'
    assert command['payload']['desired_total'] == 30000


def test_faster_mobilization_route_precedes_attack_and_updates_return_base(setup):
    coordinator, store, client, identity = begin(setup,army_ids=['a'])
    client.routes += [dict(kind='via_base',base_id='tent',travel_seconds=150,
                          prepare_seconds=10,attack_seconds=50,arrival_morale=100,verified=True)]
    coordinator.tick(now=1000)
    assert sent(client)[0]['kind'] == 'mobilize'
    client.reconciled = 'applied'
    client.statuses['a'].update(phase='stationed',current_base_id='tent',deployment_base_id='tent',position=[530,500])
    client.routes = [dict(kind='direct',base_id=None,travel_seconds=0,prepare_seconds=0,
                         attack_seconds=50,arrival_morale=100,verified=True)]
    coordinator.tick(now=1120)
    assert sent(client)[-1]['kind'] == 'attack'
    assert store.get(identity)['armies'][0]['return_base_id'] == 'tent'


def test_route_rejects_unavailable_base_low_morale_and_nonfinite_eta():
    direct = dict(kind='direct',base_id=None,travel_seconds=0,prepare_seconds=0,
                  attack_seconds=600,arrival_morale=100,verified=True,target_id='g')
    via = {**direct,'kind':'via_base','base_id':'tent','attack_seconds':10}
    base = dict(id='tent',available=False,capacity_available=True,eligible_army_ids=['a'])
    assert choose_route([direct,via],[base],'a')['kind'] == 'direct'
    base['available'] = True
    via['arrival_morale'] = 80
    assert choose_route([direct,via],[base],'a')['kind'] == 'direct'
    via.update(arrival_morale=100,attack_seconds=float('nan'))
    assert choose_route([direct,via],[base],'a')['kind'] == 'direct'


def test_radius_70_is_circle_and_never_accepts_other_npc_kind(setup):
    coordinator, store, client, identity = begin(setup,army_ids=['a'])
    client.targets = [dict(id='corner',x=570,y=570,use_code=1,conf_id=10),
                      dict(id='personal',x=510,y=510,use_code=3,conf_id=10),
                      dict(id='edge',x=570,y=500,use_code=1,conf_id=10)]
    coordinator.tick(now=1000)
    assert sent(client)[0]['payload']['target']['id'] == 'edge'


def test_timeout_restart_reconciles_command_without_resending(tmp_path):
    client = Client()
    store = HuntStore(tmp_path)
    coordinator = HuntCoordinator(store,client,world='w')
    identity = coordinator.start(request_id='all',now=1000,army_ids=['a'])['id']
    client.result = TimeoutError('transport lost')
    coordinator.tick(now=1000)
    assert len(sent(client)) == 1
    coordinator.close(); store.close()
    reopened = HuntStore(tmp_path)
    recovered = HuntCoordinator(reopened,client,world='w')
    recovered.tick(now=1120)
    assert len(sent(client)) == 1
    assert any(c[0] == 'reconcile' for c in client.calls)
    assert reopened.get(identity)['armies'][0]['pending_command_id'] is not None
    recovered.close(); reopened.close()


def test_stop_never_withdraws_and_duplicate_start_is_idempotent(setup):
    coordinator, store, client, identity = begin(setup)
    assert coordinator.start(request_id='request',now=1001)['id'] == identity
    coordinator.tick(now=1000)
    coordinator.stop(identity)
    coordinator.tick(now=1120)
    assert len(sent(client)) == 2
    assert store.get(identity)['status'] == 'paused'


def test_resource_shortage_blocks_one_team_not_others(setup):
    coordinator, store, client, identity = begin(setup)
    client.ready['a']['stamina_sufficient'] = False
    coordinator.tick(now=1000)
    assert [c['army_id'] for c in sent(client)] == ['b']


def test_stop_during_route_plan_never_creates_phantom_encounter(setup):
    coordinator, store, client, identity = begin(setup,army_ids=['a'])
    original=client.route_options
    def stop_during_plan(*args):
        coordinator.stop(identity)
        return original(*args)
    client.route_options=stop_during_plan
    coordinator.tick(now=1000)
    assert sent(client)==[]
    assert store.get(identity)['armies'][0]['encounter_open'] is False


def test_paused_campaign_keeps_target_claim_against_other_campaign(setup):
    coordinator, store, client, first = begin(setup,army_ids=['a'])
    client.targets=client.targets[:1]
    coordinator.tick(now=1000)
    coordinator.stop(first)
    coordinator.start(request_id='second',army_ids=['b'],now=1120)
    coordinator.tick(now=1120)
    assert [c['army_id'] for c in sent(client)]==['a']


@pytest.mark.parametrize('field,value',[('recruitment_state_verified',False),
    ('recruitment_active',True),('wounded_recovery_pending',True)])
def test_no_repeat_refill_while_queue_or_wounded_recovery_pending(setup,field,value):
    coordinator,store,client,identity=begin(setup,army_ids=['a'])
    for h in client.ready['a']['heroes']:h['troops']=9000
    client.ready['a'][field]=value
    coordinator.tick(now=1000)
    assert sent(client)==[]


def test_campaign_adopting_existing_battle_requires_complete_result(setup):
    coordinator,store,client=setup
    client.statuses['a'].update(phase='fighting',target_id='existing-target')
    identity=coordinator.start(request_id='all',army_ids=['a'],now=1000)['id']
    coordinator.tick(now=1000)
    client.statuses['a'].update(phase='idle',result='victory',encounter_complete=False)
    for h in client.ready['a']['heroes']:h['troops']=5000
    client.calls.clear()
    coordinator.tick(now=1120)
    assert ('ready','a') not in client.calls
    assert sent(client)==[]


def test_orphaned_journal_entry_is_recovered_instead_of_resending(setup):
    coordinator,store,client,identity=begin(setup,army_ids=['a'])
    client.result=TimeoutError('transport')
    coordinator.tick(now=1000)
    state=store.get(identity)['armies'][0]
    state.update(pending_command_id=None,encounter_open=False,target=None)
    store.save_army(identity,state)
    coordinator.tick(now=1120)
    assert len(sent(client))==1
    assert store.get(identity)['armies'][0]['pending_command_id'] is not None


def test_stop_after_command_preparation_prevents_client_send(setup,monkeypatch):
    coordinator,store,client,identity=begin(setup,army_ids=['a'])
    original=store.prepare_command
    def prepare_then_stop(*args):
        command=original(*args)
        coordinator.stop(identity)
        return command
    monkeypatch.setattr(store,'prepare_command',prepare_then_stop)
    coordinator.tick(now=1000)
    assert sent(client)==[]
    state=store.get(identity)['armies'][0]
    assert state['encounter_open'] is False and state['pending_command_id'] is None


def test_battle_seen_at_start_is_latched_before_first_tick(setup):
    coordinator,store,client=setup
    client.statuses['a'].update(phase='fighting',target_id='g1')
    identity=coordinator.start(request_id='all',army_ids=['a'],now=1000)['id']
    client.statuses['a'].update(phase='idle',encounter_complete=False)
    client.calls.clear()
    coordinator.tick(now=1000)
    assert ('ready','a') not in client.calls
    assert store.get(identity)['armies'][0]['encounter_open'] is True


def test_existing_fight_claims_target_before_idle_team_is_planned(setup):
    coordinator,store,client=setup
    client.statuses['b'].update(phase='fighting',target_id='g1')
    coordinator.start(request_id='all',now=1000)
    coordinator.tick(now=1000)
    assert sent(client)[0]['payload']['target']['id']=='g2'


def test_adopted_target_unknown_can_be_resolved_by_later_status(setup):
    coordinator,store,client=setup
    client.statuses['a'].update(phase='fighting')
    identity=coordinator.start(request_id='all',army_ids=['a'],now=1000)['id']
    coordinator.tick(now=1000)
    client.statuses['a']['target_id']='g1'
    coordinator.tick(now=1120)
    assert store.get(identity)['armies'][0]['target']['id']=='g1'
    client.statuses['a'].update(phase='idle',encounter_complete=True,completed_target_id='g1',result='victory')
    coordinator.tick(now=1240)
    assert store.get(identity)['armies'][0]['completed']==1


def test_pre_submission_stop_releases_unused_target_claim(setup):
    coordinator,store,client,first=begin(setup,army_ids=['a'])
    original=client.route_options
    def stop_during_plan(*args):
        coordinator.stop(first)
        return original(*args)
    client.route_options=stop_during_plan
    client.targets=client.targets[:1]
    coordinator.tick(now=1000)
    client.route_options=original
    coordinator.start(request_id='second',army_ids=['b'],now=1120)
    coordinator.tick(now=1120)
    assert [c['army_id'] for c in sent(client)]==['b']


def test_later_stop_wins_against_in_progress_resume(setup,monkeypatch):
    import threading
    coordinator,store,client,identity=begin(setup,army_ids=['a'])
    entered,release,stopped=threading.Event(),threading.Event(),threading.Event()
    original=store.save_army
    def delayed_save(*args):
        if threading.current_thread().name=='resume-test':
            entered.set()
            assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(store,'save_army',delayed_save)
    resumer=threading.Thread(name='resume-test',target=lambda:coordinator.resume(identity,now=1001))
    resumer.start(); assert entered.wait(2)
    def stop():
        coordinator.stop(identity)
        stopped.set()
    stopper=threading.Thread(target=stop)
    stopper.start(); stopped.wait(.05); release.set()
    resumer.join(2);stopper.join(2)
    assert not resumer.is_alive() and not stopper.is_alive()
    assert store.get(identity)['status']=='paused'


def test_camp_recruitment_journals_one_hero_per_command(setup):
    coordinator,store,client,identity=begin(setup,army_ids=['a'])
    client.statuses['a'].update(current_base_id='tent',deployment_base_id='tent')
    for h in client.ready['a']['heroes']:h['troops']=5000
    coordinator.tick(now=1000)
    command=sent(client)[0]
    assert command['kind']=='recruit'
    assert len(command['payload']['heroes'])==1
    assert command['payload']['heroes'][0]['id']==client.ready['a']['heroes'][0]['id']


def test_city_refills_low_capacity_team_but_never_sends_it_below_departure_threshold(setup):
    coordinator,store,client,identity=begin(setup,army_ids=['a'])
    client.ready['a']['heroes']=client.ready['a']['heroes'][:2]
    for hero in client.ready['a']['heroes']:
        hero.update(troops=0,capacity=7000)
    coordinator.tick(now=1000)
    assert len(sent(client))==1
    assert sent(client)[0]['kind']=='replenish'
    assert sent(client)[0]['payload']['desired_total']==14000
    client.reconciled='applied'
    for hero in client.ready['a']['heroes']:hero['troops']=7000
    client.calls.clear()
    coordinator.tick(now=1120)
    assert not sent(client)
    assert not any(row[0]=='scan' for row in client.calls)
    assert store.get(identity)['armies'][0]['reason']=='capacity_below_policy_threshold'
