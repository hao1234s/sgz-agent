"""NPC classification verified against client 1709.1907 on 2026-10-25.

conf_id is local to a configuration table. Always select that table using
use_code first; level alone and the generic wire name 'monster' are insufficient.
"""

MAP_BANDITS = {
    1: ('流寇', '游勇'), 2: ('山贼', '戎兵'), 3: ('马匪', '游骑'),
    4: ('黄巾军', '轻步卒'), 5: ('劫营骑', '铁骑兵'), 6: ('白波军', '锐武卒'),
    7: ('丹徒死士', '骁锐士'), 8: ('黑山军', '聚义军'),
    9: ('凶蛮兵', '重徒兵'), 10: ('巨戟士', '巨戟士'),
}


def classify_npc(item):
    use_code, config_id = item.get('use_code'), item.get('conf_id')
    result = {'npc_category': 'unknown', 'subtype': None, 'aliases': [],
              'display_name': item.get('name'), 'level': item.get('level')}
    if use_code == 1:
        result['npc_category'] = 'map_bandit'
        if config_id in MAP_BANDITS:
            canonical, display = MAP_BANDITS[config_id]
            result.update(subtype=canonical, aliases=list(dict.fromkeys([canonical, display])),
                          level=config_id,
                          display_name=item.get('name') if item.get('name') not in (None, 'monster')
                          else f'{display}{config_id}级')
    elif use_code == 2:
        result.update(npc_category='wooden_ox', subtype='木牛流马', aliases=['木牛流马'])
    elif use_code == 3:
        result.update(npc_category='barbarian', subtype='蛮族', aliases=['蛮族'])
    elif use_code in (4, 5, 6, 7, 1001):
        result['npc_category'] = {4: 'grain_npc', 5: 'refugee', 6: 'event_monster',
                                  7: 'camp_monster', 1001: 'one_time_monster'}[use_code]
    return result
