from datetime import datetime, timedelta, timezone

import pytest

from sgz_agent.store import Store
from sgz_agent.models import TextBox
from sgz_agent.navigation import target_fields


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path)
    yield db
    db.close()


def save(store, x, y, fields, world='w', layer='surface'):
    job = store.create_job('explore', f'{world}:{layer}:{x}:{y}:{fields}', {})
    return store.save_observation(job['id'], world, layer, x, y, fields, 'proof.png')


def test_latest_tile_is_selected_before_resource_filters(store):
    save(store, 1, 1, {'category': '铁矿', 'level': 4})
    save(store, 1, 1, {'category': '空地', 'level': None})
    save(store, 2, 1, {'category': '铁矿', 'level': 5})
    save(store, 2, 1, {'category': '铁矿', 'level': 9}, world='other')
    save(store, 3, 1, {'category': '铁矿', 'level': 9}, layer='underground')
    result = store.search_map('w', 1, 1, 3, 2, kind='resource', resource='铁矿')
    assert [(r['x'], r['y'], r['level']) for r in result['results']] == [(2, 1, 5)]
    assert result['coverage']['observed_tiles'] == 2
    assert result['coverage']['unobserved_tiles'] == 4
    assert result['results'][0]['evidence'] == 'proof.png'


def test_map_freshness_and_paging_are_explicit(store):
    old = save(store, 0, 0, {'category': '木材', 'level': 3})
    stamp = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with store.db:
        store.db.execute('UPDATE observations SET captured_at=? WHERE id=?', (stamp, old))
    save(store, 1, 0, {'category': '石料', 'level': 4})
    save(store, 2, 0, {'category': '粮食', 'level': 5})
    result = store.search_map('w', 0, 0, 2, 0, kind='resource', limit=1)
    assert result['total'] == 2 and result['next_offset'] == 1
    assert result['coverage']['stale_tiles'] == 1
    page = store.search_map('w', 0, 0, 2, 0, kind='resource', limit=1, offset=1)
    assert page['results'][0]['x'] == 2 and page['next_offset'] is None
    result = store.search_map('w', 0, 0, 2, 0, include_stale=True, max_level=3)
    assert result['results'][0]['stale'] is True


def test_bandit_overlay_does_not_lose_underlying_land(store):
    save(store, 8, 9, {'category': '空地', 'level': None,
                     'bandit': {'name': '聚义军', 'level': 8, 'garrison': {'troops_per_group': 24000, 'groups': 2}}})
    result = store.search_map('w', 8, 9, 8, 9, kind='bandit', min_level=8)
    assert result['results'][0]['kind'] == 'bandit'
    assert result['results'][0]['level'] == 8
    assert store.search_map('w', 8, 9, 8, 9, kind='resource')['total'] == 0


@pytest.mark.parametrize('kwargs', [dict(x0=True), dict(x1=-1), dict(limit=0), dict(offset=-1),
                                  dict(min_level=6, max_level=2), dict(max_age_seconds=-1),
                                  dict(kind='anything'), dict(resource='silver'), dict(include_stale='yes')])
def test_search_rejects_invalid_filters(store, kwargs):
    params = dict(world='w', x0=0, y0=0, x1=3, y1=3)
    params.update(kwargs)
    with pytest.raises(ValueError):
        store.search_map(**params)


def test_bandit_parser_requires_unique_title_in_calibrated_detail_region():
    name_region = [.4, .1, .6, .2]
    detail_region = [.1, .3, .35, .7]
    texts = [TextBox('空地', .99, (.45,.12,.55,.18)),
             TextBox('聚义军 等级8', .99, (.12,.4,.3,.45)),
             TextBox('守军兵力', .99, (.12,.5,.2,.55)),
             TextBox('24000x2', .99, (.22,.5,.32,.55)),
             TextBox('贼寇等级9', .99, (.8,.4,.9,.45))]
    fields = target_fields(texts, name_region, detail_region=detail_region)
    assert fields['bandit'] == {'name': '聚义军', 'level': 8,
                                'garrison': {'troops_per_group': 24000, 'groups': 2}}
    assert target_fields(texts, name_region)['bandit'] is None
    texts.append(TextBox('山贼等级7', .99, (.12,.6,.3,.65)))
    assert target_fields(texts, name_region, detail_region=detail_region)['bandit'] is None


def test_scan_region_larger_than_legacy_limit_keeps_resumable_points():
    from sgz_agent.workflow import region_points
    with pytest.raises(ValueError, match='region_limit_25'):
        region_points(0, 0, 5, 5)
    points = region_points(0, 0, 5, 5, max_points=2500)
    assert len(points) == len(set(points)) == 36
    assert points[6:12] == [(5,1),(4,1),(3,1),(2,1),(1,1),(0,1)]
    with pytest.raises(ValueError, match='region_limit_2500'):
        region_points(0, 0, 50, 50, max_points=2500)


def test_bandit_forces_are_not_assigned_to_underlying_resource():
    texts=[TextBox('4级铁矿',.99,(.45,.12,.55,.18)),
           TextBox('聚义军等级8',.99,(.12,.4,.3,.45)),
           TextBox('守军兵力',.99,(.12,.5,.2,.55)),TextBox('24000x2',.99,(.22,.5,.32,.55))]
    fields=target_fields(texts,[.4,.1,.6,.2],detail_region=[.1,.3,.35,.7])
    assert fields['bandit']['garrison']['troops_per_group']==24000
    assert fields['garrison'] is None


def test_legacy_and_ambiguous_bandit_observations_do_not_claim_known_kind_coverage(store):
    save(store,0,0,{'name':'空地','category':'空地'})
    save(store,1,0,{'category':'空地','bandit':None,'bandit_checked':True,'bandit_status':'unknown'})
    save(store,2,0,{'category':'空地','bandit':{'name':'聚义军','level':8},'bandit_status':'present'})
    result=store.search_map('w',0,0,3,0,kind='bandit')
    assert result['total']==1
    assert result['coverage']['observed_tiles']==3
    assert result['coverage']['kind_known_tiles']==1
    assert result['coverage']['kind_unknown_tiles']==3


def test_bandit_parser_keeps_unreadable_details_unknown():
    texts=[TextBox('空地',.99,(.45,.12,.55,.18)),TextBox('聚义军等级8',.7,(.12,.4,.3,.45))]
    fields=target_fields(texts,[.4,.1,.6,.2],detail_region=[.1,.3,.35,.7])
    assert fields['bandit_checked'] is True and fields['bandit_status']=='unknown'
    assert target_fields(texts,[.4,.1,.6,.2])['bandit_checked'] is False


def test_observed_barbarian_title_is_searchable_as_hostile_npc():
    texts=[TextBox('空地',.99,(.45,.12,.55,.18)),TextBox('蛮族部队 等级5',.99,(.12,.4,.3,.45))]
    assert target_fields(texts,[.4,.1,.6,.2],detail_region=[.1,.3,.35,.7])['bandit']['name']=='蛮族部队'
