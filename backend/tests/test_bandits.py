from sgz_agent.bandits import classify_npc


def test_giant_halberdier_is_a_map_bandit_not_a_personal_barbarian():
    result = classify_npc({'use_code': 1, 'conf_id': 10})
    assert result['npc_category'] == 'map_bandit'
    assert result['subtype'] == '巨戟士'
    assert result['level'] == 10


def test_same_config_id_in_different_table_is_not_the_same_target():
    result = classify_npc({'use_code': 3, 'conf_id': 10, 'name': '5级蛮族', 'level': 5})
    assert result['npc_category'] == 'barbarian'
    assert result['subtype'] == '蛮族'
    assert result['level'] == 5


def test_wooden_ox_is_not_a_map_bandit_and_unknowns_stay_unknown():
    assert classify_npc({'use_code': 2, 'conf_id': 1})['npc_category'] == 'wooden_ox'
    assert classify_npc({'use_code': 999, 'conf_id': 10})['subtype'] is None
    assert classify_npc({'conf_id': 10})['level'] is None


def test_season_display_name_is_preserved_alongside_canonical_name():
    result = classify_npc({'use_code': 1, 'conf_id': 8, 'name': '聚义军8级', 'level': 8})
    assert result['subtype'] == '黑山军'
    assert result['display_name'] == '聚义军8级'
    assert '聚义军' in result['aliases']
