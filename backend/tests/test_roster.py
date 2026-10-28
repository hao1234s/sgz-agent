from datetime import datetime, timedelta, timezone

import pytest

from sgz_agent.models import TextBox
from sgz_agent.store import Store


def test_roster_keeps_city_current_and_destination_coordinates_separate():
    from sgz_agent.roster import parse_roster_page
    boxes = [TextBox('主城', .99, (.1,.1,.2,.2)), TextBox('(100,200)', .99, (.3,.1,.4,.2)),
             TextBox('第一队', .99, (.1,.3,.2,.4)), TextBox('(110,205)', .99, (.3,.3,.4,.4)),
             TextBox('(120,210)', .99, (.5,.3,.6,.4)), TextBox('行军中', .99, (.7,.3,.8,.4))]
    profile = {'cities': [{'id': 'main', 'kind': 'main', 'name_region': [0,.05,.25,.25],
                           'coordinate_region': [.25,.05,.45,.25]}],
               'armies': [{'city_id': 'main', 'slot': 1, 'name_region': [0,.25,.25,.45],
                           'current_region': [.25,.25,.45,.45], 'destination_region': [.45,.25,.65,.45],
                           'status_region': [.65,.25,.85,.45]}]}
    result = parse_roster_page(boxes, profile)
    assert result['cities'][0]['coordinate'] == [100,200]
    army = result['armies'][0]
    assert army['coordinate'] == [110,205] and army['destination'] == [120,210]
    assert army['city_id'] == 'main' and army['status'] == 'marching'
    ambiguous = boxes + [TextBox('(111,205)', .99, (.3,.3,.4,.4))]
    assert parse_roster_page(ambiguous, profile)['armies'][0]['coordinate'] is None
    missing = [b for b in boxes if b.text != '(110,205)']
    assert parse_roster_page(missing, profile)['armies'][0]['coordinate'] is None


def test_roster_latest_snapshot_replaces_removed_units_and_reports_age(tmp_path):
    store = Store(tmp_path)
    try:
        first = {'cities': [{'id':'main','kind':'main','coordinate':[1,2]}],
                 'armies':[{'id':'main:1','city_id':'main','slot':1,'coordinate':[4,5]}],
                 'complete':True}
        store.save_roster('w', first, ['first.png'])
        second = {'cities': first['cities'], 'armies':[], 'complete':False, 'reason':'unreadable_row'}
        identity = store.save_roster('w', second, ['second.png'])
        result = store.query_roster('w')
        assert result['armies'] == [] and result['complete'] is False
        assert result['evidence'] == ['second.png']
        with store.db:
            store.db.execute('UPDATE roster_snapshots SET captured_at=? WHERE world=?',
                ((datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(),'w'))
        assert store.query_roster('w')['stale'] is True
        assert store.query_roster('other')['observed'] is False
    finally:
        store.close()


def test_roster_requires_observed_totals_before_claiming_complete():
    from sgz_agent.roster import combine_roster_pages
    page = {'cities':[{'id':'main','kind':'main'}],
            'armies':[{'id':'main:1','city_id':'main'}], 'expected_cities':None,'expected_armies':None,
            'unreadable_rows':0}
    assert combine_roster_pages([page])['complete'] is False
    page.update(expected_cities=1, expected_armies=1)
    assert combine_roster_pages([page])['complete'] is True
    page['expected_armies'] = 2
    assert combine_roster_pages([page])['complete'] is False


def test_city_team_counts_are_read_without_treating_capacity_as_existing_units():
    from sgz_agent.roster import parse_roster_page
    readings=[TextBox('燕',.99,(.1,.1,.2,.2)),TextBox('队0/2',.99,(.5,.1,.6,.2))]
    profile={'cities':[{'id':'sub','kind':'sub','name_region':[0,0,.3,.3],
                         'team_count_region':[.4,0,.7,.3]}]}
    city=parse_roster_page(readings,profile)['cities'][0]
    assert city['team_count']==0 and city['team_capacity']==2


def test_city_name_parts_require_each_calibrated_part_to_be_confident():
    from sgz_agent.roster import parse_roster_page
    profile={'cities':[{'id':'main','kind':'main','name_regions':[[0,0,.2,.2],[.2,0,.4,.2]],'name_separator':'|'}]}
    texts=[TextBox('秦歌',.99,(0,0,.19,.19)),TextBox('燕未还',.99,(.21,0,.39,.19))]
    assert parse_roster_page(texts,profile)['cities'][0]['name']=='秦歌|燕未还'
    assert parse_roster_page(texts[:1],profile)['cities']==[]


def test_refresh_roster_without_calibration_does_not_launch_inputs(tmp_path):
    from types import SimpleNamespace
    from sgz_agent.config import AgentConfig
    from sgz_agent.service import AgentService
    service = AgentService(AgentConfig(tmp_path/'game.exe', tmp_path, identity={'world':'w'}),
                           desktop=SimpleNamespace(release=lambda:None, list_windows=lambda exe:[]))
    try:
        with pytest.raises(RuntimeError, match='calibration_required:roster'):
            service.dispatch('refresh_roster', {'world':'w','request_id':'refresh'})
        assert service.store.list_jobs() == []
    finally:
        service.close()


def test_resumed_older_refresh_failure_is_reported_after_newer_success(tmp_path):
    store=Store(tmp_path)
    try:
        older=store.create_job('roster','older',{'world':'w'})
        store.update_job(older['id'],'paused','user_paused')
        newer=store.create_job('roster','newer',{'world':'w'})
        store.save_roster('w',{'cities':[],'armies':[],'complete':True,'position_complete':True},['ok.png'])
        store.update_job(newer['id'],'complete')
        assert store.query_roster('w')['refresh_required'] is False
        store.update_job(older['id'],'running')
        store.update_job(older['id'],'paused','identity_mismatch')
        result=store.query_roster('w')
        assert result['refresh_status']=='paused' and result['refresh_error']=='identity_mismatch'
        assert result['refresh_job_id']==older['id'] and result['refresh_required'] is True
    finally:
        store.close()
