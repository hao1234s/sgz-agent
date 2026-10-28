"""Explicit one-shot Agent trial. Does not enable unattended hunting."""
import argparse
from contextlib import closing
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import time

from .combat_assessment import assess_combat
from .config import load_config
from .hunt_attack import (AttackJournal, ControlledAttackRuntime, _assert_external_clear,
                          execute_controlled, morale_floor)
from .hunt_client import (HuntClient, _positive_id, _position, normalize_route_preview,
                          preview_request, reserve_search_sessions)
from .hunt_search import AOISearchAccumulator
from .hunt_policy import search_radius, target_profile
from .lease import DataLease


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def account_route(segments, *, initial_morale):
    """Estimate morale from evidenced path cells; discovery distance is irrelevant.

    Zero-cost movement is allowed only with an explicitly verified current rule.
    A verified rule still yields an estimate rather than a server arrival result.
    This pure calculation does not authorize or execute relocation commands.
    """
    if (not isinstance(segments, list) or not 1 <= len(segments) <= 100
            or not _number(initial_morale) or initial_morale < 0):
        raise ValueError('route_evidence_invalid')
    seconds = cells = charged = free = cost = 0
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError('route_segment_invalid')
        length, duration, per_cell = (segment.get(k) for k in
                                     ('actual_path_len', 'seconds', 'morale_cost_per_cell'))
        if (segment.get('kind') not in ('attack', 'relocate')
                or type(length) is not int or length < 0
                or segment.get('path_verified') is not True
                or not isinstance(segment.get('path_source'), str) or not segment['path_source'].strip()
                or not _number(duration) or duration < 0
                or not _number(per_cell) or per_cell < 0
                or type(segment.get('morale_rule_verified')) is not bool
                or not isinstance(segment.get('morale_rule_source'), str)
                or not segment['morale_rule_source'].strip()
                or per_cell == 0 and segment['morale_rule_verified'] is not True):
            raise ValueError('route_segment_unverified')
        seconds += duration
        cells += length
        cost += length * per_cell
        if per_cell > 0:
            charged += length
        else:
            free += length
    return {'total_seconds': seconds, 'total_actual_path_cells': cells,
            'morale_charged_path_cells': charged, 'morale_free_path_cells': free,
            'estimated_morale_cost': cost, 'estimated_arrival_morale': max(0, initial_morale-cost),
            'arrival_morale_source': 'segment_path_cell_estimate', 'arrival_morale_verified': False,
            'segments': deepcopy(segments)}


def _field_reference(config, army, inputs, *, army_id, now):
    """Verify a stationary launch and owned deployment reference, not an endpoint."""
    raw, current, base = army.get('raw_status'), inputs.get('army'), army.get('return_base_witness')
    if not all(isinstance(v, dict) for v in (raw, current, base)):
        raise ValueError('field_launch_evidence_missing')
    base_id = str(_positive_id(army.get('return_base_id')))
    dispatch = raw.get('dispatch_position')
    _position(dispatch)
    heroes = inputs.get('heroes')
    if not isinstance(heroes, list) or len(heroes) != 3:
        raise ValueError('field_launch_heroes_unverified')
    expected = [int(_positive_id(h.get('id'))) for h in heroes]
    if len(set(expected)) != 3 or army.get('phase') != 'stationed':
        raise ValueError('field_launch_heroes_unverified')
    for row in (raw, current):
        observed = row.get('observed_at')
        if (str(row.get('army_id')) != army_id or row.get('army_type') != 1
                or row.get('hero_ids') != expected or any(type(h) is not int for h in row['hero_ids'])
                or row.get('status_codes') != [3] or type(row.get('army_status')) is not int
                or type(row.get('system_status')) is not int
                or row['army_status'] != 3 or row['system_status'] != 3
                or row.get('at_dispatch') is not False or row.get('position') != army['position']
                or row.get('dispatch_position') != dispatch or row['position'] == dispatch
                or str(row.get('dispatch_build_id')) != base_id
                or not _number(observed) or not 0 <= now-observed < 20
                or not _number(row.get('dispatch_entered_at'))
                or not 0 < row['dispatch_entered_at'] <= observed
                or any(not _number(row.get(k)) or row[k] != 0
                       for k in ('movement_end_time', 'movement_total_time'))
                or any(k not in row or row[k] is not None for k in
                       ('command', 'enemy_army_id', 'fight_position', 'destination'))):
            raise ValueError('field_launch_not_verified_stationary')
    if (base.get('world') != config.identity['world'] or base.get('army_id') != army_id
            or str(base.get('id')) != base_id or base.get('position') != dispatch
            or base.get('owned') is not True
            or base.get('source') not in ('client_lua_owned_city', 'client_lua_owned_build')
            or not _number(base.get('observed_at')) or not 0 <= now-base['observed_at'] < 20):
        raise ValueError('field_launch_dispatch_reference_unverified')
    return {'launch_mode': 'stationary_field', 'return_base_id': base_id,
            'dispatch_position': dispatch, 'return_base_witness': base,
            'auto_return_mode': 'game_managed_auto_return', 'return_destination_verified': False}


def build_plan(config, army, target, inputs, preview, *, minimum_morale, now):
    radius = search_radius(config)
    profile = target_profile(config)
    if (army.get('kind') != 'regular' or army.get('formed') is not True
            or army.get('phase') not in ('idle', 'stationed')):
        raise ValueError('army_not_ready_at_base')
    army_id = str(_positive_id(army['id']))
    _position(army.get('position'))
    field = (_field_reference(config, army, inputs, army_id=army_id, now=now)
             if not army.get('current_base_id') else {})
    base_id = field['return_base_id'] if field else str(_positive_id(army['current_base_id']))
    _positive_id(target.get('id')); _position([target.get('x'), target.get('y')])
    if (type(target.get('use_code')) is not int or target['use_code'] != profile['use_code']
            or type(target.get('conf_id')) is not int or target['conf_id'] != profile['conf_id']
            or target.get('source') != 'server_aoi_refresh'
            or not _number(target.get('observed_at')) or not 0 <= now-target['observed_at'] < 20
            or not _number(target.get('end_time')) or target['end_time'] <= now
            or math.dist(army['position'], [target['x'], target['y']]) > radius):
        raise ValueError('target_stale_or_out_of_scope')
    _positive_id(target.get('army_conf_id')); _positive_id(target.get('troop_type'))
    a = inputs.get('army', {})
    if (str(a.get('army_id')) != army_id or str(a.get('dispatch_build_id')) != base_id
            or a.get('position') != army['position'] or not _number(inputs.get('observed_at'))
            or not 0 <= now-inputs['observed_at'] < 20):
        raise ValueError('army_observation_changed')
    if (type(inputs.get('orders_available')) is not int or inputs['orders_available'] < 1
            or inputs.get('cost_decree') != 1 or inputs.get('cost_energy') != 15
            or inputs.get('can_auto_return') is not True
            or type(inputs.get('defender_count')) is not int or inputs['defender_count'] != profile['defender_count']
            or type(inputs.get('target_level')) is not int or inputs['target_level'] != profile['conf_id']):
        raise ValueError('attack_cost_or_target_unverified')
    heroes = inputs.get('heroes')
    if (not isinstance(heroes, list) or len(heroes) != 3 or len({h.get('id') for h in heroes}) != 3
            or any(type(h.get('troops')) is not int or type(h.get('capacity')) is not int
                   or not 0 < h['troops'] <= h['capacity'] or not _number(h.get('stamina'))
                   or h['stamina'] < 15 or not _number(h.get('wounded_until'))
                   or h['wounded_until'] > now for h in heroes)
            or sum(h['troops'] for h in heroes) < 20000):
        raise ValueError('heroes_unready')
    if (type(preview.get('result')) is not int or preview['result'] != 1
            or not _number(preview.get('need_time')) or preview['need_time'] < 0
            or not _number(preview.get('path_len')) or preview['path_len'] < 0
            or preview.get('need_energy') != 15):
        raise ValueError('route_preview_unverified')
    preview = normalize_route_preview(preview, origin=army['position'], target=[target['x'], target['y']])
    if (not _number(inputs.get('morale')) or not _number(inputs.get('morale_cost_per_cell'))
            or inputs['morale_cost_per_cell'] < 0 or not _number(minimum_morale)
            or not 0 <= minimum_morale <= 100):
        raise ValueError('morale_parameters_invalid')
    route = None
    if preview.get('path_length_verified') is True:
        route = account_route([{'kind': 'attack', 'actual_path_len': preview['actual_path_len'],
            'seconds': preview['need_time'], 'path_verified': True,
            'path_source': preview['path_length_source'],
            'morale_cost_per_cell': inputs['morale_cost_per_cell'], 'morale_rule_verified': False,
            'morale_rule_source': 'own_army.status.cur_shiqi_cost_percell'}], initial_morale=inputs['morale'])
    arrival = (route['estimated_arrival_morale'] if route is not None else
               max(0, inputs['morale'] - preview['path_len']*inputs['morale_cost_per_cell']))
    if arrival < minimum_morale:
        raise ValueError('arrival_morale_below_authorized_floor')
    assessment = assess_combat(phase='idle', heroes=heroes, arrival_morale=arrival,
        observation_age_seconds=now-inputs['observed_at'], min_total_troops=20000,
        morale_rule_verified=False)
    return deepcopy({'world':config.identity['world'], 'army_id':army_id, 'search_radius':radius,
        'target_profile':profile,
        'base_id':base_id, 'position':army['position'], 'target':target,
        'heroes':heroes, 'preview':preview, 'created_at':now, 'expires_at':now+20,
        'min_troops':20000, 'min_morale':minimum_morale, 'stamina_cost':15,
        'orders_available':inputs['orders_available'], 'morale_cost_per_cell':inputs['morale_cost_per_cell'],
        'arrival_morale':arrival, 'defenders_verified':True,
        'preflight_morale':inputs['morale'],
        'arrival_morale_verified':False,
        'arrival_morale_source':(route['arrival_morale_source'] if route is not None
                                 else 'client_current_morale_minus_preview_path_estimate'),
        'morale_charged_path_cells':route['morale_charged_path_cells'] if route is not None else None,
        'route_accounting':route,
        'defenders':{'count':profile['defender_count'], 'troops_each':None, 'army_conf_id':target['army_conf_id'],
                     'troop_type':target['troop_type']}, 'assessment':assessment, **field})


def fresh_target(config, raw, target_id, position, *, now):
    if raw.get('cleanup_verified') is not True or raw.get('overflow') or raw.get('cancelled'):
        raise ValueError('target_refresh_unclean')
    profile = target_profile(config)
    acc = AOISearchAccumulator(world=config.identity['world'], center=position, radius=1,
                              target_conf_id=profile['conf_id'])
    extra = {}
    for s in raw['sessions']:
        acc.begin(session_id=s['session_id'], center=s['center'], sent_at=s['sent_at'])
        for e in raw['events']:
            if e['data'].get('session_id') != s['session_id']:
                continue
            if not acc.ingest(e['name'], e['data'], received_at=e['received_at']):
                continue
            for row in e['data'].get('info_list', []):
                if str(row.get('id')) == str(target_id):
                    extra = row.get('monster', {})
        acc.finish(session_id=s['session_id'], finished_at=s['finished_at'])
    target = next((t for t in acc.result(now=now)['targets'] if t['id'] == str(target_id)
                   and [t['x'], t['y']] == position), None)
    if target is None:
        raise ValueError('target_no_longer_present')
    return {**target, 'army_conf_id':extra.get('army_conf_id'), 'troop_type':extra.get('troop_type'),
            'monster_id':extra.get('monter_id')}


def prepare_controlled(config, *, army_id, target_id, position):
    army_id, target_id = str(_positive_id(army_id)), str(_positive_id(target_id))
    _position(position)
    with closing(DataLease(config.data_dir)), AttackJournal(config.data_dir) as journal:
        _assert_external_clear(config, army_id)
        if any(r['army_id'] == army_id or r['target_id'] == target_id for r in journal.pending(config.identity['world'])):
            raise RuntimeError('attack_pending_no_new_preflight')
        client = HuntClient(config, controlled_verification=True)
        army = next(a for a in client.roster_status()['armies'] if a['id'] == army_id)
        if army['phase'] not in ('idle','stationed') or not army.get('current_base_id'):
            raise ValueError('army_not_ready_at_base')
        floor = morale_floor(config)
        profile = target_profile(config)
        partial = {'world':config.identity['world'], 'army_id':army_id, 'min_morale':floor,
                   'target_profile':profile,
                   'target':{'id':target_id,'x':position[0],'y':position[1],
                             'use_code':profile['use_code'],'conf_id':profile['conf_id']}}
        runtime = ControlledAttackRuntime(config, plan=partial, controlled_verification=True)
        try:
            runtime.start()
            inputs = runtime.request('attack_inputs')
        finally:
            runtime.close()
        final = runtime.state()
        if final['phase'] != 'closed' or final['fault'] or final['cleanup_fault'] or final['bridge_retained']:
            raise RuntimeError('attack_inputs_cleanup_unconfirmed')
        preview = client._read('preview', request=preview_request(army_id, position))['preview']
        raw = client._read('search_fresh', army_id=int(army_id), center=position, radius=1,
            centers=[position], session_seed=reserve_search_sessions(config,1), dwell_ms=1000,timeout_ms=10000)
        now = time.time()
        target = fresh_target(config, raw, target_id, position, now=now)
        plan = build_plan(config, army, target, inputs, preview, minimum_morale=floor, now=now)
        return {'plan':plan, 'inputs':inputs, 'target_refresh':raw, 'inputs_runtime_final':final}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=str(Path(__file__).resolve().parents[2] / 'config/game.local.json'))
    parser.add_argument('--army-id',required=True)
    parser.add_argument('--target-id',required=True)
    parser.add_argument('--x',type=int,required=True)
    parser.add_argument('--y',type=int,required=True)
    parser.add_argument('--request-id',required=True)
    parser.add_argument('--approved-controlled-session',action='store_true',required=True)
    parser.add_argument('--execute',action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',args.request_id):
        raise ValueError('request_id_invalid')
    config = load_config(args.config)
    with closing(DataLease(config.data_dir)), AttackJournal(config.data_dir) as journal:
        row = journal.db.execute('SELECT * FROM attacks WHERE request_id=?',(args.request_id,)).fetchone()
        if row:
            record = journal._decode(row)
            if (record['world'] != config.identity['world'] or record['army_id'] != args.army_id
                    or record['target_id'] != args.target_id):
                raise ValueError('request_identity_conflict')
            print(json.dumps({'cached':True,'receipt':record['result'],'status':record['status']}));return
    directory = config.data_dir/'hunt-trials'/args.request_id
    directory.mkdir(parents=True,exist_ok=True)
    prepared = prepare_controlled(config,army_id=args.army_id,target_id=args.target_id,position=[args.x,args.y])
    (directory/'preflight.json').write_text(json.dumps(prepared,ensure_ascii=False,indent=2),encoding='utf-8')
    plan = prepared['plan']
    result = {'preflight':str(directory/'preflight.json'),'army_id':plan['army_id'],
              'arrival_morale':plan['arrival_morale'],'sent':False}
    if args.execute:
        receipt = execute_controlled(config,request_id=args.request_id,plan=plan)
        (directory/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        result.update(receipt={k:v for k,v in receipt.items() if k != 'events'})
        result['sent'] = receipt.get('sent')
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
