"""Explicit one-shot Agent trial. Does not enable unattended hunting."""
import argparse
from contextlib import closing
from copy import deepcopy
import json
import math
import re
import time

from .combat_assessment import assess_combat
from .config import load_config
from .hunt_attack import (AttackJournal, ControlledAttackRuntime, _assert_external_clear,
                          execute_controlled, morale_floor)
from .hunt_client import HuntClient, _positive_id, _position, preview_request, reserve_search_sessions
from .hunt_search import AOISearchAccumulator
from .lease import DataLease


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def build_plan(config, army, target, inputs, preview, *, minimum_morale, now):
    if (army.get('kind') != 'regular' or army.get('formed') is not True
            or army.get('phase') not in ('idle', 'stationed') or not army.get('current_base_id')):
        raise ValueError('army_not_ready_at_base')
    army_id, base_id = str(_positive_id(army['id'])), str(_positive_id(army['current_base_id']))
    _position(army.get('position'))
    _positive_id(target.get('id')); _position([target.get('x'), target.get('y')])
    if (target.get('use_code') != 1 or target.get('conf_id') != 10
            or target.get('source') != 'server_aoi_refresh'
            or not _number(target.get('observed_at')) or not 0 <= now-target['observed_at'] < 20
            or not _number(target.get('end_time')) or target['end_time'] <= now
            or math.dist(army['position'], [target['x'], target['y']]) > 70):
        raise ValueError('target_stale_or_out_of_scope')
    _positive_id(target.get('army_conf_id')); _positive_id(target.get('troop_type'))
    a = inputs.get('army', {})
    if (str(a.get('army_id')) != army_id or str(a.get('dispatch_build_id')) != base_id
            or a.get('position') != army['position'] or not _number(inputs.get('observed_at'))
            or not 0 <= now-inputs['observed_at'] < 20):
        raise ValueError('army_observation_changed')
    if (type(inputs.get('orders_available')) is not int or inputs['orders_available'] < 1
            or inputs.get('cost_decree') != 1 or inputs.get('cost_energy') != 15
            or inputs.get('can_auto_return') is not True or inputs.get('defender_count') != 3
            or inputs.get('target_level') != 10):
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
    if (not _number(inputs.get('morale')) or not _number(inputs.get('morale_cost_per_cell'))
            or inputs['morale_cost_per_cell'] < 0 or not _number(minimum_morale)
            or not 0 <= minimum_morale <= 100):
        raise ValueError('morale_parameters_invalid')
    arrival = max(0, inputs['morale'] - preview['path_len']*inputs['morale_cost_per_cell'])
    if arrival < minimum_morale:
        raise ValueError('arrival_morale_below_authorized_floor')
    assessment = assess_combat(phase='idle', heroes=heroes, arrival_morale=arrival,
        observation_age_seconds=now-inputs['observed_at'], min_total_troops=20000,
        morale_rule_verified=False)
    return deepcopy({'world':config.identity['world'], 'army_id':army_id,
        'base_id':base_id, 'position':army['position'], 'target':target,
        'heroes':heroes, 'preview':preview, 'created_at':now, 'expires_at':now+20,
        'min_troops':20000, 'min_morale':minimum_morale, 'stamina_cost':15,
        'orders_available':inputs['orders_available'], 'morale_cost_per_cell':inputs['morale_cost_per_cell'],
        'arrival_morale':arrival, 'defenders_verified':True,
        'defenders':{'count':3, 'troops_each':None, 'army_conf_id':target['army_conf_id'],
                     'troop_type':target['troop_type']}, 'assessment':assessment})


def fresh_target(config, raw, target_id, position, *, now):
    if raw.get('cleanup_verified') is not True or raw.get('overflow') or raw.get('cancelled'):
        raise ValueError('target_refresh_unclean')
    acc = AOISearchAccumulator(world=config.identity['world'], center=position, radius=1)
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
        partial = {'world':config.identity['world'], 'army_id':army_id, 'min_morale':floor,
                   'target':{'id':target_id,'x':position[0],'y':position[1],'use_code':1,'conf_id':10}}
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
    parser.add_argument('--config',default='config/game.local.json')
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
