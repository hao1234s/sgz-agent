"""Identity-bound live adapter for one explicitly requested dispatch round.

The validation record authorizes controlled fixed-command verification; it is
not evidence of unattended logistics or victory. The caller owns DataLease.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import threading
import time

from .hunt_runtime import _controlled_readiness
from .hunt_attack import (AttackJournal, ControlledAttackRuntime, _assert_external_clear,
                          execute_under_lease, morale_floor)
from .hunt_client import HuntClient, normalize_army, preview_request, reserve_search_sessions
from .hunt_search import AOISearchAccumulator, plan_query_centers
from .hunt_trial import build_plan, fresh_target
from .hunt_policy import search_radius, target_profile


def _binding(config):
    directory=Path(__file__).parent
    names=('hunt_dispatch_client.py','hunt_dispatch.py','hunt_attack.py','hunt_attack.js',
           'hunt_client.py','hunt_client.js','hunt_runtime.py','hunt_runtime.js',
           'hunt_trial.py','hunt_search.py','hunt_search.js','hunt_search_bridge.js',
           'hunt_observer.js','client_api.py','replenish_client.py','hunt_policy.py','hunt_retarget.py',
           'native_lifecycle.js','native_session.py')
    digest=hashlib.sha256()
    for name in names:
        digest.update(name.encode());digest.update((directory/name).read_bytes())
    marker=config.data_dir/'hunt-client-quarantine.json'
    return {'identity':dict(config.identity),'implementation':digest.hexdigest(),
            'executable':str(config.executable),
            'incident':hashlib.sha256(marker.read_bytes()).hexdigest() if marker.exists() else 'none'}


def _clean(final):
    return (isinstance(final,dict) and final.get('phase')=='closed'
            and 'fault' in final and final['fault'] is None
            and 'cleanup_fault' in final and final['cleanup_fault'] is None
            and final.get('bridge_retained') is False
            and isinstance(final.get('generation'), str) and bool(final['generation'].strip()))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _formation_key(ids):
    if (not isinstance(ids, list) or len(ids) != 3
            or any(isinstance(h, bool) or not str(h).isascii() or not str(h).isdigit()
                   or not 0 < int(h) < 2**53 for h in ids)
            or len({int(h) for h in ids}) != 3):
        raise ValueError('army_formation_unverified')
    return 'heroes:' + ','.join(str(int(h)) for h in ids)


def _plan_formation(plan):
    heroes = plan.get('heroes')
    if not isinstance(heroes, list) or any(not isinstance(h, dict) for h in heroes):
        raise ValueError('army_formation_unverified')
    return _formation_key([h.get('id') for h in heroes])


def _bind_return_base(army, raw, world, now):
    """A fresh owned reference, with no capacity or automatic endpoint claim."""
    try:
        status = army['raw_status']
        identity, position = str(status['dispatch_build_id']), status['dispatch_position']
        if (not identity.isascii() or not identity.isdigit() or int(identity) <= 0
                or not isinstance(position, list) or len(position) != 2
                or any(type(v) is not int or not 1 <= v <= 1620 for v in position)
                or not isinstance(raw, dict) or raw.get('cleanup_verified') is not True
                or not _number(raw.get('observed_at')) or not 0 <= now-raw['observed_at'] <= 30
                or not isinstance(raw.get('bases'), list)):
            raise ValueError('return_base_unverified')
        matches = [row for row in raw['bases'] if isinstance(row, dict) and row.get('id') == identity]
        if (len(matches) != 1 or matches[0].get('position') != position
                or matches[0].get('source') not in {'client_lua_owned_city', 'client_lua_owned_build'}
                or matches[0].get('creating') is True or matches[0].get('destroying') is True):
            raise ValueError('return_base_unverified')
        result = deepcopy(army)
        result['return_base_id'] = identity
        result['return_base_witness'] = dict(world=world, army_id=army['id'], id=identity,
            position=deepcopy(position), source=matches[0]['source'], owned=True, observed_at=raw['observed_at'])
        return result
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('return_base_unverified') from error


def _validate_evidence(config, evidence, verified_at):
    """Rebuild the saved preflight at its original time, without native calls."""
    try:
        identity = config.identity
        if (not isinstance(evidence, dict) or evidence.get('identity') != identity
                or any(not isinstance(identity.get(k), str) or not identity[k].strip()
                       for k in ('world', 'role_id', 'region', 'api_server_id'))
                or type(identity.get('api_season')) is not int or identity['api_season'] <= 0
                or not _clean(evidence.get('runtime_final'))
                or evidence.get('runtime_generation') != evidence['runtime_final']['generation']):
            raise ValueError('unbound_native_evidence')
        plan, army, inputs = evidence['plan'], evidence['army'], evidence['inputs']
        created_at = plan['created_at']
        if (not _number(verified_at) or not _number(created_at)
                or not 0 <= verified_at - created_at < 20
                or not _number(plan.get('expires_at')) or plan['expires_at'] <= verified_at
                or plan.get('world') != identity['world']
                or army.get('stable_key') != _plan_formation(plan)
                or _formation_key(inputs['army']['hero_ids']) != army['stable_key']
                or _plan_formation(inputs) != army['stable_key']
                or normalize_army(inputs['army'])['phase'] not in ('idle', 'stationed')):
            raise ValueError('unbound_preflight')
        target = plan['target']
        if army.get('current_base_id') is None:
            bound = _bind_return_base(army, evidence['return_base_read'], identity['world'], created_at)
            if bound['return_base_witness'] != army.get('return_base_witness'):
                raise ValueError('return_base_unbound')
        refreshed = fresh_target(config, evidence['target_refresh'], target['id'],
                                 [target['x'], target['y']], now=created_at)
        rebuilt = build_plan(config, army, refreshed, inputs, plan['preview'],
                             minimum_morale=plan['min_morale'], now=created_at)
        rebuilt['return_base_id'] = rebuilt['base_id']
        # Canonical JSON also distinguishes booleans from numeric guard values.
        if json.dumps(plan, sort_keys=True, allow_nan=False) != json.dumps(rebuilt, sort_keys=True, allow_nan=False):
            raise ValueError('preflight_not_reproduced')
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, OverflowError) as error:
        raise ValueError('dispatch_verification_evidence_invalid') from error


def save_dispatch_validation(config, *, preflight_evidence, authorization):
    verified_at = time.time()
    if not isinstance(authorization, str) or not authorization.strip():
        raise ValueError('dispatch_verification_evidence_invalid')
    _validate_evidence(config, preflight_evidence, verified_at)
    record={'binding':_binding(config),'verified_at':verified_at,
            'preflight_evidence':deepcopy(preflight_evidence),'authorization':authorization,
            'scope':'controlled_single_round','unattended_logistics':False}
    path=config.data_dir/'hunt-dispatch-validation.json'
    temporary=path.with_suffix('.pending')
    temporary.write_text(json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)
    return record


class LiveDispatchClient:
    def __init__(self,config,stop_event=None,*,controlled_verification=False):
        self.config=config
        self.stop_event=stop_event or threading.Event()
        self.controlled_verification=controlled_verification is True
        self.client=HuntClient(config,stop_event=self.stop_event,controlled_verification=True)

    def capabilities(self):
        reason=_controlled_readiness(self.config)
        missing=[reason] if reason else []
        if not self.controlled_verification:
            try:
                record=json.loads((self.config.data_dir/'hunt-dispatch-validation.json').read_text(encoding='utf-8'))
                if (not isinstance(record, dict) or record.get('binding')!=_binding(self.config)
                        or record.get('scope')!='controlled_single_round'
                        or record.get('unattended_logistics') is not False
                        or not isinstance(record.get('authorization'), str) or not record['authorization'].strip()
                        or not _number(record.get('verified_at'))
                        or not 0 <= time.time()-record['verified_at'] <= 3600):
                    raise ValueError('dispatch_validation_changed_or_expired')
                _validate_evidence(self.config, record.get('preflight_evidence'), record['verified_at'])
            except (OSError,ValueError,TypeError):missing.append('dispatch_validation_required')
        return {'available':not missing,'read_available':not missing,'missing':missing,
                'scope':'controlled_single_round','continuous_available':False}

    def _require(self,scope):
        if self.stop_event.is_set():raise RuntimeError('stopped')
        cap=self.capabilities()
        if not cap['available']:raise RuntimeError(';'.join(cap['missing']))

    def roster_status(self):
        self._require('read')
        raw=self.client._read('roster')
        rows=[]
        for item in raw['armies']:
            army=normalize_army(item)
            heroes=item.get('hero_ids',[])
            if len(heroes)==3 and len(set(heroes))==3 and all(type(h) is int and h>0 for h in heroes):
                army['stable_key']='heroes:'+','.join(str(h) for h in heroes)
            army['raw_status']=item
            rows.append(army)
        return {'world':self.config.identity['world'],'complete':raw.get('complete') is True,
                'armies':rows,'observed_at':raw.get('observed_at')}

    def candidates(self,army):
        self._require('read')
        position=army['position']
        radius=search_radius(self.config)
        profile=target_profile(self.config)
        # A bounded server query supplies candidates, not a coverage assertion.
        # Short separately closed chunks reduce exposure to unrelated UI refresh.
        # Older positive samples are discovery hints only, never send evidence.
        windows=self._candidate_windows(position,radius)
        discovered={}
        self.last_candidate_diagnostics={'sampled_centers':0,'search_radius':radius,
            'coverage_verified':False,'target_profile':profile,'tentative_count':0,'dropped':[]}
        if profile['conf_id']==9:
            near=[position]+[[position[0]+dx,position[1]+dy] for dx,dy in ((20,0),(-20,0),(0,20),(0,-20))
                             if 1<=position[0]+dx<=1620 and 1<=position[1]+dy<=1620]
            raw=self._search_read(army['id'],center=position,radius=70,centers=near,
                                  dwell_ms=1500,timeout_ms=20000)
            self._merge_candidate_samples(raw,discovered,position,radius,profile)
            self.last_candidate_diagnostics['sampled_centers']=len(near)
            initial=self._candidate_hints(discovered,position)
            if len(initial)>=3:
                self.last_candidate_diagnostics['tentative_count']=len(initial)
                confirmed=self._confirm_candidates(army,initial[:6])
                if len(confirmed)>=3:return confirmed
        for center,centers in windows:
            raw=self._search_read(army['id'],center=center,radius=70,
                centers=centers,dwell_ms=1500,timeout_ms=20000)
            self._merge_candidate_samples(raw,discovered,position,radius,profile)
            self.last_candidate_diagnostics['sampled_centers']+=len(centers)
        tentative=self._candidate_hints(discovered,position)
        self.last_candidate_diagnostics['tentative_count']=len(tentative)
        return self._confirm_candidates(army,tentative,limit=6 if profile['conf_id']==9 else None)

    def candidate_batches(self,army):
        """Yield one fresh discovery window at a time; selected targets need prepare.

        Every read is closed before yielding. The coordinator can close this
        iterator after one unreserved candidate without scanning or confirming
        unrelated targets. Sampling never asserts nearest-target or coverage.
        """
        self._require('read')
        position=army['position']
        radius,profile=search_radius(self.config),target_profile(self.config)
        near=[position]+[[position[0]+dx,position[1]+dy] for dx,dy in ((20,0),(-20,0),(0,20),(0,-20))
                         if 1<=position[0]+dx<=1620 and 1<=position[1]+dy<=1620]
        visited={tuple(point) for point in near}
        following=[]
        for center,points in self._candidate_windows(position,radius):
            remaining=[point for point in points if tuple(point) not in visited]
            if not remaining:continue
            visited.update(tuple(point) for point in remaining)
            remaining.sort(key=lambda point:math.dist(position,point))
            following.append((center,remaining))
        following.sort(key=lambda window:min(math.dist(position,point) for point in window[1]))
        self.last_candidate_diagnostics=dict(source='candidate_batches',sampled_centers=0,windows=0,
            search_radius=radius,target_profile=profile,coverage_verified=False,discovery_candidates=0)
        seen=set()
        for center,points in [(position,near),*following]:
            raw=self._search_read(army['id'],center=center,radius=70,centers=points,
                                  dwell_ms=1500,timeout_ms=20000)
            discovered={}
            self._merge_candidate_samples(raw,discovered,position,radius,profile)
            now=time.time()
            candidates=[target for target in self._candidate_hints(discovered,position)
                        if target['id'] not in seen and 0<=now-target['observed_at']<=30
                        and target['end_time']>now]
            seen.update(target['id'] for target in candidates)
            self.last_candidate_diagnostics['sampled_centers']+=len(points)
            self.last_candidate_diagnostics['windows']+=1
            self.last_candidate_diagnostics['discovery_candidates']+=len(candidates)
            yield candidates

    def _merge_candidate_samples(self,raw,discovered,position,radius,profile):
        for session in raw.get('sessions',[]):
            acc=AOISearchAccumulator(world=self.config.identity['world'],center=session['center'],radius=70,
                                     max_age=120,target_conf_id=profile['conf_id'])
            acc.begin(session_id=session['session_id'],center=session['center'],sent_at=session['sent_at'])
            for event in raw.get('events',[]):
                if event['data'].get('session_id')==session['session_id']:
                    acc.ingest(event['name'],event['data'],received_at=event['received_at'])
            acc.finish(session_id=session['session_id'],finished_at=session['finished_at'])
            for target in acc.result(now=time.time())['targets']:
                if math.dist(position,[target['x'],target['y']])>radius:continue
                previous=discovered.get(target['id'])
                if previous is None or target['observed_at']>previous['observed_at']:
                    discovered[target['id']]=target

    def _candidate_hints(self,discovered,position):
        now=time.time()
        return sorted((target for target in discovered.values()
            if 0<=now-target['observed_at']<=120 and target['end_time']>now),
            key=lambda target:(math.dist(position,[target['x'],target['y']]),target['id']))

    def _confirm_candidates(self,army,tentative,limit=None):
        confirmed=[]
        for target in tentative:
            point=[target['x'],target['y']]
            raw=self._search_read(army['id'],center=point,radius=1,centers=[point],
                                  dwell_ms=2000,timeout_ms=10000)
            try:
                current=fresh_target(self.config,raw,target['id'],point,now=time.time())
            except ValueError as error:
                if str(error)!='target_no_longer_present':raise
                self.last_candidate_diagnostics['dropped'].append({'id':target['id'],'reason':str(error)})
                continue
            if any(current.get(k)!=target[k] for k in ('id','x','y','use_code','conf_id','end_time')):
                self.last_candidate_diagnostics['dropped'].append({'id':target['id'],'reason':'pinned_target_changed'})
                continue
            confirmed.append(current)
            if limit is not None and len(confirmed)>=limit:break
        now=time.time()
        return [target for target in confirmed
                if 0<=now-target['observed_at']<=30 and target['end_time']>now]

    def _candidate_windows(self,position,radius):
        if radius==70:
            points=list(reversed(plan_query_centers(position,radius=70,spacing=20)))
            return [(position,points[offset:offset+8]) for offset in range(0,len(points),8)]
        # A fixed 49-point sample for the explicitly authorized 140 circle.
        # Each native window still obeys its unchanged 70-circle validation.
        points=[[position[0]+dx,position[1]+dy] for dx in range(-140,141,35)
                for dy in range(-140,141,35) if dx*dx+dy*dy<=140**2
                and 1<=position[0]+dx<=1620 and 1<=position[1]+dy<=1620]
        points.sort(key=lambda p:(math.dist(position,p),p),reverse=True)
        windows=[]
        while points:
            center=points.pop(0)
            nearby=[p for p in points if math.dist(center,p)<=70][:7]
            for point in nearby:points.remove(point)
            windows.append((center,[center,*nearby]))
        return windows

    def _search_read(self,army_id,*,center,radius,centers,dwell_ms,timeout_ms):
        for attempt in range(2):
            self._require('read')
            try:
                raw=self.client._read('search_fresh',army_id=int(army_id),center=center,radius=radius,
                    centers=centers,session_seed=reserve_search_sessions(self.config,len(centers)),
                    dwell_ms=dwell_ms,timeout_ms=timeout_ms)
            except RuntimeError as error:
                # _read closes its native session in finally. A failed close
                # replaces this reason and therefore can never enter this retry.
                if attempt==0 and str(error)=='hunt_search_foreign_refresh_observed':continue
                raise
            if raw.get('cleanup_verified') is not True or raw.get('overflow') or raw.get('cancelled'):
                raise RuntimeError('candidate_refresh_unclean')
            return raw

    def prepare(self,army,target):
        self._require('dispatch')
        if not isinstance(army.get('stable_key'), str):
            raise ValueError('army_formation_unverified')
        return self._prepare(army['id'],target,expected_stable_key=army['stable_key'])

    def _verify_return_base(self, army):
        self._last_return_base_read = None
        if army.get('current_base_id') is not None:
            return army
        if army.get('phase') != 'stationed':
            raise ValueError('return_base_unverified')
        raw = self.client._read('bases')
        bound = _bind_return_base(army, raw, self.config.identity['world'], time.time())
        self._last_return_base_read = deepcopy(raw)
        return bound

    def _prepare(self,army_id,target,*,expected_stable_key=None):
        profile=target_profile(self.config)
        if target.get('use_code')!=profile['use_code'] or target.get('conf_id')!=profile['conf_id']:
            raise ValueError('target_profile_changed')
        _assert_external_clear(self.config,army_id)
        with AttackJournal(self.config.data_dir) as journal:
            if any(r['army_id']==army_id or r['target_id']==target['id'] for r in journal.pending(self.config.identity['world'])):
                raise RuntimeError('attack_pending_no_new_preflight')
        army=next(a for a in self.roster_status()['armies'] if a['id']==army_id)
        if army['phase'] not in ('idle','stationed'):raise ValueError('army_not_ready_for_details')
        stable_key = _formation_key(army['raw_status']['hero_ids'])
        if (army.get('stable_key') != stable_key
                or expected_stable_key is not None and stable_key != expected_stable_key):
            raise ValueError('army_formation_changed')
        army = self._verify_return_base(army)
        floor=morale_floor(self.config)
        partial={'world':self.config.identity['world'],'army_id':army_id,'min_morale':floor,
                 'target':target,'target_profile':profile}
        runtime=ControlledAttackRuntime(self.config,plan=partial,controlled_verification=True,stop_event=self.stop_event)
        try:
            runtime.start();inputs=runtime.request('attack_inputs')
        finally:runtime.close()
        final=runtime.state()
        if not _clean(final):raise RuntimeError('attack_inputs_cleanup_unconfirmed')
        if (_plan_formation(inputs) != stable_key
                or _formation_key(inputs['army']['hero_ids']) != stable_key):
            raise ValueError('army_formation_changed')
        position=[target['x'],target['y']]
        preview=self.client._read('preview',request=preview_request(army_id,position))['preview']
        raw=self._search_read(army_id,center=position,radius=1,
            centers=[position],dwell_ms=2000,timeout_ms=10000)
        now=time.time()
        refreshed=fresh_target(self.config,raw,target['id'],position,now=now)
        for key in ('id','x','y','use_code','conf_id','end_time'):
            if key in target and refreshed.get(key)!=target[key]:raise ValueError('pinned_target_changed')
        plan=build_plan(self.config,army,refreshed,inputs,preview,minimum_morale=floor,now=now)
        plan['return_base_id']=plan['base_id']
        self.last_preflight=deepcopy({'identity':dict(self.config.identity),
            'runtime_generation':runtime.generation,'army':army,'plan':plan,'inputs':inputs,
            'runtime_final':final,'target_refresh':raw})
        if self._last_return_base_read is not None:
            self.last_preflight['return_base_read'] = deepcopy(self._last_return_base_read)
        return plan

    def execute(self,plan,request_id):
        identity={'world':plan['world'],'army_id':plan['army_id'],'target_id':plan['target']['id']}
        # All preparation still precedes the durable native claim. A failure here
        # is known unsent; exceptions after execute_under_lease stay unknown.
        try:
            self._require('dispatch')
            if plan['world'] != self.config.identity['world']:
                raise ValueError('attack_world_mismatch')
            stable_key = _plan_formation(plan)
            fresh=self._prepare(plan['army_id'],deepcopy(plan['target']),expected_stable_key=stable_key)
            if (_plan_formation(fresh) != stable_key
                    or any(fresh.get(k) != plan.get(k) for k in ('world', 'army_id', 'base_id',
                        'return_base_id', 'position', 'dispatch_position', 'launch_mode',
                        'auto_return_mode', 'return_destination_verified', 'target_profile'))
                    or any(fresh['target'].get(k) != plan['target'].get(k)
                           for k in ('id', 'x', 'y', 'use_code', 'conf_id', 'end_time'))):
                raise ValueError('pinned_command_identity_changed')
        except Exception as error:
            return {**identity,'status':'rejected','sent':False,'reason':str(error)}
        return {**execute_under_lease(self.config,request_id=request_id,plan=fresh,
                                     stop_event=self.stop_event),**identity}
