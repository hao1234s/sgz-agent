"""Pure, formation-independent preflight assessment; never observes or commands a game.

Morale is a standard-rules calculation. The community troop curve is a diagnostic
component model, NOT a battle simulator. Recommendations require whole-encounter
evidence supplied by the caller with the same rules, build and defender profile.
"""
from collections import Counter
from math import ceil, isfinite, log2


MODEL = 'sgz-combat-evidence-v1'
READY_PHASES = {'idle', 'resolved'}
WAIT_PHASES = {'marching', 'fighting', 'draw', 'unknown', 'returning', 'recruiting', 'resting'}


def _number(value, name, minimum=0, maximum=10_000_000, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value) or not minimum <= value <= maximum
            or integer and int(value) != value):
        raise ValueError(f'invalid_{name}')
    return value


def _morale(value):
    _number(value, 'morale', maximum=1000)
    return 1 - .007 * max(0, 100 - value)


def _heroes(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 3:
        raise ValueError('invalid_heroes')
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip():
            raise ValueError('invalid_hero_id')
        if row['id'] in result or type(row.get('is_main')) is not bool:
            raise ValueError('invalid_hero_identity')
        capacity = _number(row.get('capacity'), 'capacity', minimum=1, integer=True)
        _number(row.get('troops'), 'troops', maximum=capacity, integer=True)
        if row.get('attribute_advantage') is not None:
            _number(row['attribute_advantage'], 'attribute_advantage', minimum=-1_000_000)
        result[row['id']] = row
    if sum(row['is_main'] for row in rows) != 1:
        raise ValueError('invalid_main_general')
    return result


def _context(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('invalid_context')
    for key in ('ruleset', 'team_build', 'target_profile'):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f'invalid_context_{key}')
    _number(value.get('defender_waves'), 'defender_waves', minimum=1, maximum=100, integer=True)
    return value


def _base(n, advantage=0):
    # Only used above 2000, since player sources disagree near the low-troop knee.
    return max(advantage + ceil(100 * (log2(n) - 9)), 0) + min(n / 50, 100)


def assess_combat(*, phase, heroes=None, arrival_morale=None,
                  observation_age_seconds=None, morale_rule_verified=False,
                  min_total_troops=0, context=None, reports=None):
    result = {'model': MODEL, 'win_probability': None, 'game_actions': 0}
    if phase in WAIT_PHASES:
        return {**result, 'decision': 'wait', 'next_step': 'wait_for_phase_resolution',
                'reason': 'No troop evaluation or new queries before the current phase ends.'}
    if phase not in READY_PHASES:
        raise ValueError('invalid_phase')
    threshold = _number(min_total_troops, 'min_total_troops', integer=True)
    if type(morale_rule_verified) is not bool:
        raise ValueError('invalid_morale_rule_verified')
    context = _context(context)
    uncertainties = []
    result.update(decision='insufficient_evidence', next_step='complete_preflight_evidence',
                  uncertainties=uncertainties,
                  policy={'min_total_troops': threshold, 'minimum_supporting_wins': 3,
                          'maximum_observation_age_seconds': 300},
                  evidence={'supporting_win_ids': [], 'stronger_loss_ids': [],
                            'comparable_reports': 0, 'ignored_reports': [],
                            'provenance': 'caller_supplied_not_independently_verified'})
    fresh = observation_age_seconds is not None
    if fresh:
        fresh = _number(observation_age_seconds, 'observation_age_seconds') <= 300
    if not fresh:
        uncertainties.append('missing_or_stale_observation')
    if context is None:
        uncertainties.append('missing_comparison_context')
    factor = None
    if arrival_morale is not None:
        factor = _morale(arrival_morale)
        result['morale'] = {'arrival': arrival_morale, 'outgoing_damage_factor': factor,
                            'outgoing_damage_loss_percent': (1 - factor) * 100,
                            'scope': 'standard_rules_outgoing_damage_only',
                            'rule_verified': morale_rule_verified}
    else:
        uncertainties.append('missing_arrival_morale')
    if not morale_rule_verified:
        uncertainties.append('morale_rule_unverified_for_ruleset')
    if heroes is None:
        uncertainties.append('missing_per_general_troops')
        return result
    current = _heroes(heroes)
    total = sum(row['troops'] for row in heroes)
    result['troops'] = {'total': total, 'capacity': sum(row['capacity'] for row in heroes),
                        'below_policy_threshold': total < threshold}
    result['heroes'] = []
    for row in heroes:
        n, cap = row['troops'], row['capacity']
        diagnostic = dict(id=row['id'], is_main=row['is_main'], troops=n,
                          capacity_fraction=n / cap, troop_component_ratio=None,
                          conditional_output_ratio=None)
        if n > 2000 and cap > 2000:
            diagnostic['troop_component_ratio'] = _base(n) / _base(cap)
            if row.get('attribute_advantage') is not None and factor is not None:
                diagnostic['conditional_output_ratio'] = (
                    _base(n, row['attribute_advantage']) / _base(cap, row['attribute_advantage']) * factor)
        else:
            uncertainties.append(f'low_troop_model_uncertain:{row["id"]}')
        result['heroes'].append(diagnostic)
    result['model_limits'] = [
        'Troop-component ratios are a disputed community model, not total damage or win probability.',
        'Conditional output compares this general with its full capacity at >=100 morale, '
        'holding the supplied attribute advantage fixed and excluding other modifiers.',
        'Troops represent the current troop pool; incoming mitigation, control, healing, '
        'targeting and later defender waves are not simulated.',
        'No additive team output score; each general and the main general must be assessed separately.',
    ]
    # Freshness is checked before suggesting any troop-based action.
    if not fresh:
        return result
    if any(row['troops'] == 0 for row in heroes):
        result.update(decision='not_ready', next_step='restore_general_readiness')
        return result
    if total < threshold:
        result.update(decision='replenish', next_step='return_to_verified_replenishment_point')
        return result
    if factor is None or not morale_rule_verified or context is None:
        return result
    if reports is None:
        reports = []
    if not isinstance(reports, list) or len(reports) > 100:
        raise ValueError('invalid_reports')
    if any(not isinstance(row, dict) for row in reports):
        raise ValueError('invalid_report')
    ids = Counter(row.get('id') for row in reports if isinstance(row, dict) and isinstance(row.get('id'), str))
    seen = set()
    evidence = result['evidence']
    comparable_losses = []
    for row in reports:
        report_id = row.get('id')
        if not isinstance(report_id, str) or not report_id.strip():
            raise ValueError('invalid_report_id')
        if report_id in seen:
            continue
        seen.add(report_id)
        if ids[report_id] > 1 and any(other != row for other in reports if other.get('id') == report_id):
            evidence['ignored_reports'].append({'id': report_id, 'reason': 'conflicting_duplicate'})
            continue
        reason = None
        if not isinstance(row.get('source'), str) or not row['source'].strip():
            reason = 'missing_source'
        elif row.get('encounter_complete') is not True or row.get('result') not in {'victory', 'defeat'}:
            reason = 'not_a_final_encounter_result'
        elif row.get('context') != context:
            reason = 'different_rules_build_or_target'
        if reason:
            evidence['ignored_reports'].append({'id': report_id, 'reason': reason})
            continue
        completed = _number(row.get('completed_waves'), 'completed_waves',
                            maximum=context['defender_waves'], integer=True)
        if row['result'] == 'victory' and completed != context['defender_waves']:
            evidence['ignored_reports'].append({'id': report_id, 'reason': 'partial_defender_victory'})
            continue
        historical = _heroes(row.get('heroes'))
        if current.keys() != historical.keys() or any(
                current[k]['capacity'] != historical[k]['capacity']
                or current[k]['is_main'] != historical[k]['is_main'] for k in current):
            evidence['ignored_reports'].append({'id': report_id, 'reason': 'different_composition'})
            continue
        historical_factor = _morale(row.get('arrival_morale'))
        evidence['comparable_reports'] += 1
        if row['result'] == 'victory':
            if factor >= historical_factor and all(current[k]['troops'] >= historical[k]['troops'] for k in current):
                evidence['supporting_win_ids'].append(report_id)
        else:
            comparable_losses.append(report_id)
            if factor <= historical_factor and all(current[k]['troops'] <= historical[k]['troops'] for k in current):
                evidence['stronger_loss_ids'].append(report_id)
    if evidence['stronger_loss_ids']:
        result.update(decision='high_risk', next_step='improve_preparation_then_reassess')
    elif len(evidence['supporting_win_ids']) >= 3 and not comparable_losses:
        result.update(decision='supported_attempt', next_step='check_remaining_expedition_requirements')
    else:
        uncertainties.append('insufficient_or_conflicting_full_encounter_evidence')
    return result
