from copy import deepcopy

import pytest

from sgz_agent.combat_assessment import assess_combat


def request(troops=(9000, 9000, 9000), morale=100):
    return dict(phase='idle', observation_age_seconds=0, arrival_morale=morale,
                morale_rule_verified=True, min_total_troops=20000,
                context={'ruleset': 'test-season-build', 'team_build': 'arbitrary-build-v1',
                         'target_profile': 'npc-variant-1', 'defender_waves': 3},
                heroes=[dict(id=f'hero-{i}', is_main=i == 0, troops=n, capacity=10000)
                        for i, n in enumerate(troops)], reports=[])


def report(report_id='r1', troops=(8000, 8000, 8000), morale=100, **overrides):
    req = request(troops, morale)
    return dict(id=report_id, source=f'local-report:{report_id}', context=req['context'],
                heroes=req['heroes'], arrival_morale=morale, result='victory',
                encounter_complete=True, completed_waves=3, **overrides)


@pytest.mark.parametrize('morale,factor', [(120, 1), (100, 1), (96, .972),
                                          (90, .93), (80, .86), (50, .65), (0, .3)])
def test_morale_changes_outgoing_damage_not_incoming_damage(morale, factor):
    result = assess_combat(**request(morale=morale))
    assert result['morale']['outgoing_damage_factor'] == pytest.approx(factor)
    assert result['win_probability'] is None
    assert 'effective_troops' not in result
    assert result['decision'] == 'insufficient_evidence'


@pytest.mark.parametrize('phase', ['marching', 'fighting', 'draw', 'unknown', 'returning', 'recruiting'])
def test_phase_gate_precedes_even_invalid_or_missing_troop_inputs(phase):
    result = assess_combat(phase=phase, heroes='do not inspect')
    assert result['decision'] == 'wait'
    assert 'troops' not in result and 'morale' not in result
    assert result['next_step'] == 'wait_for_phase_resolution'


def test_per_hero_distribution_and_main_survival_are_not_hidden_by_total():
    depleted = assess_combat(**request((2000, 10000, 10000)))
    balanced = assess_combat(**request((8000, 7000, 7000)))
    assert depleted['troops']['total'] == balanced['troops']['total'] == 22000
    assert depleted['heroes'][0]['capacity_fraction'] == .2
    assert balanced['heroes'][0]['capacity_fraction'] == .8
    assert depleted['heroes'][0]['troop_component_ratio'] is None
    assert 'low_troop_model_uncertain:hero-0' in depleted['uncertainties']


def test_nonlinear_component_and_morale_are_computed_per_general():
    args = request((5000, 10000, 10000), 80)
    args['heroes'][0]['attribute_advantage'] = 0
    result = assess_combat(**args)
    # Community model only: ceil(100*(log2(5000)-9))+100 = 429;
    # at 10000: 529. Never treat this component index as combat win chance.
    assert result['heroes'][0]['troop_component_ratio'] == pytest.approx(429 / 529)
    assert result['heroes'][0]['conditional_output_ratio'] == pytest.approx(429 / 529 * .86)
    assert result['heroes'][1]['conditional_output_ratio'] is None
    assert result['decision'] == 'insufficient_evidence'


def test_threshold_is_explicit_policy_not_a_generic_20000_requirement():
    args = request((6000, 6000, 6000))
    assert assess_combat(**args)['decision'] == 'replenish'
    args['min_total_troops'] = 15000
    assert assess_combat(**args)['decision'] == 'insufficient_evidence'
    args['heroes'][0]['troops'] = 0
    assert assess_combat(**args)['decision'] == 'not_ready'


def test_full_comparable_encounters_can_support_attempt_for_any_build():
    args = request()
    args['reports'] = [report(f'r{i}') for i in range(3)]
    result = assess_combat(**args)
    assert result['decision'] == 'supported_attempt'
    assert result['evidence']['supporting_win_ids'] == ['r0', 'r1', 'r2']
    assert result['win_probability'] is None


def test_lower_arrival_morale_cannot_reuse_high_morale_wins():
    args = request(morale=80)
    args['reports'] = [report(f'r{i}') for i in range(3)]
    assert assess_combat(**args)['decision'] == 'insufficient_evidence'
    for row in args['reports']:
        row['arrival_morale'] = 70
    assert assess_combat(**args)['decision'] == 'supported_attempt'


def test_no_synthetic_winning_vector_from_minima_across_reports():
    args = request((8000, 8000, 8000))
    args['reports'] = [report('a', (7000, 9000, 9000)), report('b', (9000, 7000, 9000)),
                       report('c', (9000, 9000, 7000))]
    assert assess_combat(**args)['evidence']['supporting_win_ids'] == []


def test_deduplicates_reports_and_rejects_partial_or_different_config_evidence():
    args = request()
    partial = report('partial')
    partial['encounter_complete'] = False
    first_wave = report('first-wave')
    first_wave['completed_waves'] = 1
    changed = report('changed')
    changed['context']['team_build'] = 'different-tactics'
    args['reports'] = [report(), report(), partial, first_wave, changed]
    result = assess_combat(**args)
    assert result['decision'] == 'insufficient_evidence'
    assert result['evidence']['supporting_win_ids'] == ['r1']


def test_loss_under_at_least_current_strength_overrides_wins():
    args = request()
    loss = report('loss', (10000, 10000, 10000))
    loss['result'] = 'defeat'
    loss['completed_waves'] = 1
    args['reports'] = [report(f'r{i}') for i in range(3)] + [loss]
    result = assess_combat(**args)
    assert result['decision'] == 'high_risk'
    assert result['evidence']['stronger_loss_ids'] == ['loss']


@pytest.mark.parametrize('field,value', [('observation_age_seconds', 301),
                                       ('arrival_morale', None),
                                       ('morale_rule_verified', False), ('context', None)])
def test_missing_or_stale_data_does_not_gain_recommendation(field, value):
    args = request()
    args['reports'] = [report(f'r{i}') for i in range(3)]
    args[field] = value
    assert assess_combat(**args)['decision'] == 'insufficient_evidence'


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -1, True, '80'])
def test_invalid_numeric_input_is_rejected(bad):
    args = request(morale=bad)
    with pytest.raises(ValueError):
        assess_combat(**args)


def test_inputs_unchanged_and_hero_identity_order_independent():
    args = request()
    args['reports'] = [report(f'r{i}') for i in range(3)]
    args['reports'][0]['heroes'].reverse()
    before = deepcopy(args)
    assert assess_combat(**args)['decision'] == 'supported_attempt'
    assert args == before


def test_invalid_composition_is_rejected():
    args = request()
    args['heroes'][1]['id'] = args['heroes'][0]['id']
    with pytest.raises(ValueError):
        assess_combat(**args)


def test_conflicting_report_id_is_not_counted_as_supporting_evidence():
    args = request()
    conflict = report('r0')
    conflict['result'] = 'defeat'
    args['reports'] = [report(f'r{i}') for i in range(3)] + [conflict]
    result = assess_combat(**args)
    assert result['decision'] == 'insufficient_evidence'
    assert result['evidence']['supporting_win_ids'] == ['r1', 'r2']
    assert result['evidence']['ignored_reports'][0]['reason'] == 'conflicting_duplicate'


def test_malformed_report_list_is_a_validation_error():
    args = request()
    args['reports'] = [report(), report(), None]
    with pytest.raises(ValueError, match='invalid_report'):
        assess_combat(**args)


def test_no_replenishment_from_stale_or_in_battle_troop_observation():
    args = request((1000, 1000, 1000))
    args['observation_age_seconds'] = 301
    assert assess_combat(**args)['decision'] == 'insufficient_evidence'
    args['phase'] = 'draw'
    assert assess_combat(**args)['decision'] == 'wait'
