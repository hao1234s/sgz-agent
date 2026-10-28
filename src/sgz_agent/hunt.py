"""Persistent multi-army coordinator. Game concurrency, serialized verified commands."""
import math
import threading
import time


WAIT_PHASES = {'marching','fighting','draw','unknown','returning','relocating','recruiting','resting'}
READY_PHASES = {'idle','stationed','resolved'}
POLICY = dict(radius=70, radius_metric='coordinate_circle', poll_seconds=120,
              min_troops=20000, min_arrival_morale=100, allow_insist=True)


def _finite(value, minimum=0):
    return type(value) in (int,float) and math.isfinite(value) and value >= minimum


def _eligible(base, army_id):
    return (base.get('available') is True and base.get('capacity_available') is True
            and army_id in base.get('eligible_army_ids', []))


def choose_route(options, bases, army_id, min_morale=100):
    locations = {b['id']:b for b in bases}
    eligible = []
    for option in options:
        if option.get('verified') is not True or option.get('kind') not in {'direct','via_base'}:
            continue
        times = [option.get(k) for k in ('travel_seconds','prepare_seconds','attack_seconds')]
        if not all(_finite(t) for t in times) or not _finite(option.get('arrival_morale'),min_morale):
            continue
        if option['kind'] == 'via_base' and not _eligible(locations.get(option.get('base_id'),{}),army_id):
            continue
        eligible.append({**option,'total_seconds':sum(times)})
    return min(eligible,key=lambda r:(r['total_seconds'],r['kind'] != 'direct',str(r.get('base_id')))) if eligible else None


class HuntCoordinator:
    def __init__(self, store, client, *, world, clock=time.time):
        self.store, self.client, self.world, self.clock = store, client, world, clock
        self._tick_lock = threading.Lock()
        self._submission_lock = threading.RLock()
        self._closed = threading.Event()
        self._thread = None

    def start_background(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop,name='sgz-hunts',daemon=True)
            self._thread.start()

    def _loop(self):
        while not self._closed.wait(1):
            try: self.tick()
            except Exception: pass  # Individual campaign failures are recorded by tick.

    def start(self, *, request_id, army_ids=None, now=None):
        with self._tick_lock:
            return self._start(request_id=request_id,army_ids=army_ids,now=now)

    def _start(self, *, request_id, army_ids, now):
        if not isinstance(request_id,str) or not request_id.strip(): raise ValueError('request_id_required')
        if army_ids is not None and (not isinstance(army_ids,list) or not army_ids
                or any(not isinstance(i,str) or not i.strip() for i in army_ids)
                or len(set(army_ids)) != len(army_ids)):
            raise ValueError('invalid_army_ids')
        selection = sorted(army_ids) if army_ids is not None else 'all_regular_armies'
        previous = self.store.find_request(request_id)
        if previous:
            if previous['selection'] != selection or previous['world'] != self.world:
                raise ValueError('request_conflict')
            return previous
        capabilities = self.client.capabilities()
        if capabilities.get('available') is not True:
            raise RuntimeError('hunt_capabilities_missing:'+','.join(capabilities.get('missing',[])))
        roster = self.client.roster_status()
        if roster.get('world') != self.world: raise RuntimeError('world_mismatch')
        if army_ids is None and roster.get('complete') is not True: raise RuntimeError('roster_incomplete')
        rows = roster.get('armies',[])
        regular = {a['id']:a for a in rows if a.get('kind') == 'regular'
                   and a.get('formed') is not False and a.get('home_city_id')}
        selected = sorted(regular) if army_ids is None else sorted(army_ids)
        if not selected or any(i not in regular for i in selected): raise ValueError('no_eligible_armies')
        return self.store.create(request_id,self.world,selection,selected,POLICY,
                                 self.clock() if now is None else now,initial_statuses=regular)

    def status(self, hunt_id=None):
        return {'capabilities':self.client.capabilities(),
                'campaigns':[self.store.get(hunt_id)] if hunt_id else self.store.list()}

    def stop(self, hunt_id):
        with self._submission_lock:
            self.store.get(hunt_id)
            self.store.update(hunt_id,status='paused',reason='user_stopped:no_game_withdrawal')
            return self.store.get(hunt_id)

    def resume(self, hunt_id, now=None):
        with self._tick_lock, self._submission_lock:
            return self._resume(hunt_id,now)

    def _resume(self, hunt_id, now):
        campaign = self.store.get(hunt_id)
        if campaign['world'] != self.world: raise ValueError('world_mismatch')
        capabilities = self.client.capabilities()
        if capabilities.get('available') is not True:
            raise RuntimeError('hunt_capabilities_missing:'+','.join(capabilities.get('missing',[])))
        if any(c['id'] != hunt_id and c['status'] == 'running' for c in self.store.list()):
            raise RuntimeError('hunt_already_running')
        for state in campaign['armies']:
            state['blocked'] = False
            self.store.save_army(hunt_id,state)
        self.store.update(hunt_id,status='running',reason='reconcile_before_continuing',
                          next_poll_at=self.clock() if now is None else now)
        return self.store.get(hunt_id)

    def tick(self, now=None):
        now = self.clock() if now is None else now
        if self._closed.is_set() or not self._tick_lock.acquire(blocking=False): return
        try:
            for campaign in self.store.list():
                if campaign['status'] != 'running' or now < campaign['next_poll_at']: continue
                identity = campaign['id']
                self.store.update(identity,next_poll_at=now+campaign['policy']['poll_seconds'])
                try:
                    if campaign['world'] != self.world: raise RuntimeError('world_mismatch')
                    capabilities = self.client.capabilities()
                    if capabilities.get('available') is not True: raise RuntimeError('client_capability_unavailable')
                    roster = self.client.roster_status()  # Status-only, never a troop refresh.
                    if roster.get('world') != self.world: raise RuntimeError('world_mismatch')
                    rows = roster.get('armies',[])
                    statuses = {a['id']:a for a in rows}
                    if len(statuses) != len(rows): raise RuntimeError('duplicate_army_identity')
                    # Adopt every observed battle before any idle army chooses a target.
                    # Persist the latch even when the next observation is an ambiguous idle.
                    for state in campaign['armies']:
                        status = statuses.get(state['army_id'],{})
                        if status.get('phase') not in {'fighting','draw'}: continue
                        if not state['encounter_open']:
                            state['encounter_open'] = state['adopted_encounter'] = True
                            state['target'] = None
                        if state.get('adopted_encounter') and state['target'] is None and status.get('target_id'):
                            state['target'] = {'id':status['target_id']}
                        if state['target']:
                            self.store.reserve(identity,state['target']['id'],state['army_id'])
                        self.store.save_army(identity,state)
                    bases = None
                    scans = {}
                    for state in campaign['armies']:
                        if self._closed.is_set() or self.store.get(identity)['status'] != 'running': break
                        try:
                            status = statuses.get(state['army_id'])
                            if status is None:
                                state['reason'] = 'army_not_observed'
                                continue
                            state['phase'] = status.get('phase','unknown')
                            if not self._reconcile(campaign,state,status,now): continue
                            if state.get('blocked'): continue
                            if state['phase'] not in READY_PHASES:
                                state['reason'] = 'wait_for_phase_resolution'
                                continue
                            if state['encounter_open']:
                                if not (status.get('encounter_complete') is True
                                        and state['target'] is not None
                                        and status.get('completed_target_id') == state['target']['id']
                                        and status.get('result') in {'victory','defeat'}):
                                    state['reason'] = 'wait_for_whole_encounter_result'
                                    continue
                                state['encounter_open'] = False
                                state['adopted_encounter'] = False
                                if status['result'] == 'victory': state['completed'] += 1
                                state['last_result'] = status['result']
                                state['target'] = None
                                self.store.release(identity,state['army_id'])
                                self.store.event(identity,state['army_id'],now,{'result':status['result']})
                            ready = self.client.ready_army(state['army_id'])
                            if ready.get('id') != state['army_id'] or ready.get('phase') not in READY_PHASES:
                                state['reason'] = 'army_changed_before_readiness'
                                continue
                            if bases is None: bases = self.client.bases()
                            self._ready(campaign,state,ready,bases,scans,now)
                        except Exception as error:
                            state['reason'] = f'{type(error).__name__}:{error}'
                            self.store.event(identity,state['army_id'],now,{'error':state['reason']})
                        finally:
                            self.store.save_army(identity,state)
                    self.store.update(identity,reason=None)
                except Exception as error:
                    self.store.update(identity,reason=f'{type(error).__name__}:{error}')
                    self.store.event(identity,None,now,{'error':str(error)})
        finally: self._tick_lock.release()

    def _reconcile(self, campaign, state, status, now):
        command_id = state.get('pending_command_id')
        if command_id is None:
            orphan = self.store.outstanding(campaign['id'],state['army_id'])
            if orphan is None: return True
            command_id = state['pending_command_id'] = orphan['id']
            if orphan['kind'] == 'attack':
                state['encounter_open'] = True
                state['target'] = orphan['payload']['target']
        command = self.store.command(command_id)
        outcome = command['status']
        if outcome not in {'applied','not_applied','rejected'}:
            outcome = self.client.reconcile(command,status)
            if outcome not in {'applied','not_applied'}:
                state['reason'] = 'command_result_unknown:no_resend'
                return False
            self.store.finish_command(command_id,outcome)
        state['pending_command_id'] = None
        if outcome != 'applied':
            receipt = command.get('result') or {}
            if (outcome == 'not_applied' and command['kind'] in {'replenish','recruit'}
                    and receipt.get('sent') is False and receipt.get('retryable') is True):
                state['reason'] = receipt.get('reason','wait_for_recruitment_resources')
                return False
            state['blocked'] = True
            state['reason'] = 'command_not_applied:review_before_resume'
            if command['kind'] in {'attack','mobilize'}:
                state['encounter_open'] = False
                state['target'] = None
                self.store.release(campaign['id'],state['army_id'])
            return False
        if command['kind'] == 'mobilize':
            state['return_base_id'] = command['payload']['base_id']
        return True

    def _issue(self, campaign, state, kind, payload, now):
        with self._submission_lock:
            if self._closed.is_set() or self.store.get(campaign['id'])['status'] != 'running':
                self._discard_unsent_target(campaign,state,kind)
                return
            command = self.store.prepare_command(campaign['id'],state,kind,payload,now)
            if command is None:
                self._discard_unsent_target(campaign,state,kind)
                return
            if self._closed.is_set() or self.store.get(campaign['id'])['status'] != 'running':
                self.store.finish_command(command['id'],'not_applied',{'sent':False})
                state['pending_command_id'] = None
                if kind == 'attack': state['encounter_open'] = False
                self._discard_unsent_target(campaign,state,kind)
                state['reason'] = 'stopped_before_send'
                return
            try:
                receipt = self.client.execute(command)
                status = receipt.get('status')
                if status not in {'accepted','rejected','unknown','applied','not_applied'}: status = 'unknown'
                self.store.finish_command(command['id'],status,receipt)
            except Exception as error:
                self.store.finish_command(command['id'],'unknown',{'error':f'{type(error).__name__}:{error}'})
            state['reason'] = 'await_command_confirmation'

    def _discard_unsent_target(self, campaign, state, kind):
        if kind in {'attack','mobilize'} and not state.get('pending_command_id') and not state['encounter_open']:
            self.store.release(campaign['id'],state['army_id'])
            state['target'] = None
            state.pop('route',None)

    def _ready(self, campaign, state, data, bases, scans, now):
        identity, army_id, policy = campaign['id'], state['army_id'], campaign['policy']
        if data.get('ready') is not True:
            state['reason'] = 'army_not_ready'; return
        if (data.get('recruitment_state_verified') is not True
                or data.get('recruitment_active') is not False
                or data.get('wounded_recovery_pending') is not False):
            state['reason'] = 'wait_for_verified_recruitment_and_wounded_recovery'; return
        heroes = data.get('heroes')
        if not isinstance(heroes,list) or not 1 <= len(heroes) <= 3:
            state['reason'] = 'per_general_troops_unknown'; return
        if (any(not _finite(h.get('troops')) or not _finite(h.get('capacity'),1)
                or h['troops'] > h['capacity'] for h in heroes)
                or sum(h.get('is_main') is True for h in heroes) != 1
                or len({h.get('id') for h in heroes}) != len(heroes)):
            state['reason'] = 'invalid_per_general_troops'; return
        troops, capacity = sum(h['troops'] for h in heroes),sum(h['capacity'] for h in heroes)
        morale = data.get('morale')
        if not _finite(morale): state['reason'] = 'morale_unknown'; return
        base_id = data.get('deployment_base_id') or data.get('home_city_id')
        state['return_base_id'] = base_id
        locations = {b['id']:b for b in bases}
        current_base = locations.get(data.get('current_base_id'))
        low = troops < policy['min_troops'] or morale < policy['min_arrival_morale'] or any(h['troops'] == 0 for h in heroes)
        if not current_base and (low or state.get('refill_required')):
            base = locations.get(base_id)
            if base is None or not _eligible(base,army_id) or base.get('can_recruit') is not True:
                state['reason'] = 'return_base_unverified_or_unavailable'; return
            state['refill_required'] = troops < policy['min_troops']
            self._issue(campaign,state,'return',{'base_id':base_id,'expected_phase':data['phase']},now)
            return
        if current_base:
            if not _eligible(current_base,army_id): state['reason'] = 'base_permission_unverified'; return
            city = current_base.get('kind') in {'main','sub'}
            # A city is a refill location, not a reason to refill an otherwise
            # ready force. Once a low-troop refill starts, finish that workflow.
            needs_fill = troops < capacity and (troops < policy['min_troops'] or state.get('refill_required'))
            if needs_fill:
                if current_base.get('can_recruit') is not True:
                    state['reason'] = 'base_cannot_replenish'; return
                state['refill_required'] = True
                # Camp protocol mutates one hero. Persist each request separately;
                # never send an unjournaled batch whose result could be partial.
                refill_heroes = heroes if city else [next(h for h in heroes if h['troops'] < h['capacity'])]
                self._issue(campaign,state,'replenish' if city else 'recruit',
                            {'base_id':current_base['id'],'desired_total':capacity,
                             'heroes':refill_heroes,'expected_phase':data['phase']},now)
                return
            if troops >= capacity: state['refill_required'] = False
            if capacity < policy['min_troops']:
                state['reason'] = 'capacity_below_policy_threshold'; return
            if low:
                state['reason'] = 'wait_for_morale_or_general_recovery'; return
        if data.get('stamina_sufficient') is not True or data.get('orders_sufficient') is not True:
            state['reason'] = 'wait_for_stamina_or_orders'; return
        if data.get('combat_assessment',{}).get('decision') in {'high_risk','not_ready','replenish','wait'}:
            state['reason'] = 'combat_assessment_requires_preparation'; return
        center = data.get('position')
        if (not isinstance(center,(list,tuple)) or len(center) != 2
                or not all(type(n) is int and 1 <= n <= 1620 for n in center)):
            state['reason'] = 'current_position_unknown'; return
        key = tuple(center)
        if key not in scans: scans[key] = self.client.search_giants(list(center),policy['radius'],army_id=army_id)
        scan = scans[key]
        if scan.get('complete') is not True:
            state['reason'] = 'giant_search_coverage_incomplete'; return
        targets = [t for t in scan.get('targets',[]) if t.get('use_code') == 1 and t.get('conf_id') == 10
                   and isinstance(t.get('id'),str) and all(type(t.get(k)) is int for k in ('x','y'))
                   and (t['x']-center[0])**2+(t['y']-center[1])**2 <= policy['radius']**2]
        choices = []
        for target in targets:
            options = self.client.route_options(data,target,bases)
            options = [r for r in options if r.get('target_id') == target['id']]
            route = choose_route(options,bases,army_id,policy['min_arrival_morale'])
            if route: choices.append((route['total_seconds'],target['id'],target,route))
        for _,_,target,route in sorted(choices,key=lambda r:(r[0],r[1])):
            if not self.store.reserve(identity,target['id'],army_id): continue
            old_target = state.get('target')
            if old_target and old_target['id'] != target['id']:
                self.store.release(identity,army_id)
                if not self.store.reserve(identity,target['id'],army_id): continue
            state['target'] = target
            state['route'] = route
            if route['kind'] == 'via_base' and route['base_id'] != data.get('current_base_id'):
                self._issue(campaign,state,'mobilize',{'base_id':route['base_id'],'target':target,
                            'base_position':locations[route['base_id']].get('position'),
                            'route':route,'expected_phase':data['phase']},now)
            else:
                self._issue(campaign,state,'attack',{'target':target,'route':route,
                            'return_base_id':state['return_base_id'],'allow_insist':policy['allow_insist'],
                            'expected_phase':data['phase']},now)
            return
        state['reason'] = 'no_unreserved_feasible_giant_within_radius'

    def close(self):
        self._closed.set()
        if self._thread is not None: self._thread.join()
