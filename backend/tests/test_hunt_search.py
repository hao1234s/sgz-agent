"""Offline AOI search contracts: no native attachment, UI, or network."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from sgz_agent.hunt_search import AOISearchAccumulator, plan_query_centers


def target(identity=1, x=100, y=100, end=200, **changes):
    return {'id': identity, 'proto_type': 'add', 'type': 10, 'monster': {
        'use_code': 1, 'conf_id': 10, 'pos': {'row': x, 'col': y},
        'end_time': end, **changes}}


def search():
    result = AOISearchAccumulator(world='w', center=[100, 100], radius=70, max_age=30)
    result.begin(session_id=11, center=[100, 100], sent_at=100)
    return result


def feed(acc, rows, session=11, timestamp=101):
    return acc.ingest('sc_notify_refresh_aoi_content',
                      {'session_id': session, 'info_list': rows}, received_at=timestamp)


def test_end_counts_and_target_positions_never_imply_coverage():
    acc = search()
    feed(acc, [target()])
    for _ in range(8):
        acc.ingest('sc_notify_refresh_aoi_end', {'session_id': 11, 'num': 8}, received_at=102)
    result = acc.result(now=103)
    assert result['complete'] is False
    assert result['coverage']['verified_tiles'] == 0
    assert result['coverage']['required_tiles'] == 15373
    assert result['coverage_reason'] == 'server_region_coverage_unverified'
    assert result['targets'][0]['id'] == '1'
    assert result['coverage']['sessions'][0]['end_markers'] == 8


def test_wrong_session_or_pre_request_data_cannot_supply_targets():
    acc = search()
    assert feed(acc, [target()], session=99) is False
    assert feed(acc, [target()], timestamp=99) is False
    assert acc.result(now=103)['targets'] == []


def test_queries_are_serial_and_completed_session_does_not_accept_late_data():
    acc = search()
    with pytest.raises(ValueError, match='session_pending'):
        acc.begin(session_id=12, center=[120, 100], sent_at=101)
    acc.finish(session_id=11, finished_at=102)
    acc.begin(session_id=12, center=[120, 100], sent_at=103)
    assert feed(acc, [target()], timestamp=104) is False
    feed(acc, [target(2)], session=12, timestamp=104)
    assert [t['id'] for t in acc.result(now=105)['targets']] == ['2']
    with pytest.raises(ValueError, match='session_reused'):
        acc.begin(session_id=11, center=[100, 100], sent_at=105)


@pytest.mark.parametrize('row', [
    target(end=103), target(end=0), target(end=None), target(x=171),
    target(x=150, y=150), target(use_code=2), target(conf_id=9),
    target(identity=True), target(x=True), target(end=float('nan')),
    target(use_code=True), target(conf_id=10.0),
])
def test_expired_unknown_or_outside_circle_targets_are_excluded(row):
    acc = search()
    feed(acc, [row])
    assert acc.result(now=103)['targets'] == []


def test_later_delete_and_invalid_updates_remove_previously_seen_target():
    acc = search()
    feed(acc, [target()])
    feed(acc, [{'id': 1, 'proto_type': 'del'}], timestamp=102)
    assert acc.result(now=103)['targets'] == []
    feed(acc, [target()], timestamp=103)
    feed(acc, [target(conf_id=9)], timestamp=104)
    assert acc.result(now=105)['targets'] == []


def test_explicit_nine_search_accepts_only_ordinary_heavy_infantry():
    acc=AOISearchAccumulator(world='w',center=[100,100],target_conf_id=9)
    acc.begin(session_id=11,center=[100,100],sent_at=100)
    feed(acc,[target(1,conf_id=10),target(2,conf_id=9,use_code=3),target(3,conf_id=9)])
    result=acc.result(now=103)
    assert [row['id'] for row in result['targets']]==['3']
    assert result['targets'][0]['use_code']==1 and result['targets'][0]['conf_id']==9
    assert result['complete'] is False


@pytest.mark.parametrize('conf_id',[8,11,9.0,True,None])
def test_search_profile_cannot_enable_other_npc_kinds(conf_id):
    with pytest.raises(ValueError,match='target'):
        AOISearchAccumulator(world='w',center=[100,100],target_conf_id=conf_id)


def test_stale_and_truncated_content_is_not_relabelled_fresh():
    acc = search()
    feed(acc, {'1': target(), 'truncated': True})
    assert acc.result(now=102)['targets'] == []
    feed(acc, [target()], timestamp=103)
    assert acc.result(now=134)['targets'] == []


def test_plan_is_bounded_deterministic_and_includes_circle_edges_without_claiming_coverage():
    points = plan_query_centers([100, 100], 70, spacing=30)
    assert points[0] == [100, 100]
    assert len(points) == len({tuple(p) for p in points})
    assert all((x-100)**2 + (y-100)**2 <= 70**2 for x, y in points)
    assert all([200-x, 200-y] in points for x, y in points)
    assert all(point in points for point in [[30, 100], [170, 100], [100, 30], [100, 170]])
    assert all(1 <= x <= 1620 and 1 <= y <= 1620 for x, y in plan_query_centers([1, 1], 70))
    with pytest.raises(ValueError, match='query_budget_exceeded'):
        plan_query_centers([100, 100], 70, spacing=1)


def test_javascript_collector_serializes_queries_and_captures_only_matching_sessions():
    node = shutil.which('node')
    assert node
    source = Path(__file__).resolve().parents[1] / 'src/sgz_agent/client/hunt_search.js'
    harness = r'''
const {collectAOISearch} = require(process.argv[1]);
let listener, clock=100, active=0, maxActive=0, requests=[];
(async()=>{
  const result=await collectAOISearch({centers:[[100,100],[130,100]],sessionSeed:11,dwellMs:10}, {
    now:()=>clock,
    subscribe:fn=>{listener=fn;return ()=>{listener=null;};},
    sendRefresh:args=>{active++;maxActive=Math.max(active,maxActive);requests.push(args);
      listener('sc_notify_refresh_aoi_content',{session_id:999,info_list:[]});
      listener('sc_notify_refresh_aoi_content',{session_id:args.session_id,info_list:[]});},
    sleep:async ms=>{clock+=ms/1000;active--;},
  });
  console.log(JSON.stringify({result,maxActive,requests,unsubscribed:listener===null}));
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
    output = subprocess.run([node, '-e', harness, str(source)], capture_output=True,
                            text=True, timeout=10)
    assert output.returncode == 0, output.stderr
    result = json.loads(output.stdout)
    assert result['maxActive'] == 1
    assert result['unsubscribed'] is True
    assert [q['session_id'] for q in result['requests']] == [11, 12]
    assert len(result['result']['events']) == 2
    assert result['result']['complete'] is False
    assert all(s['reason'] == 'bounded_collection_window_coverage_unknown'
               for s in result['result']['sessions'])


@pytest.mark.parametrize('mode', ['cancelled', 'failed'])
def test_javascript_collector_stops_sending_and_always_unsubscribes(mode):
    node = shutil.which('node')
    assert node
    source = Path(__file__).resolve().parents[1] / 'src/sgz_agent/client/hunt_search.js'
    harness = r'''
const {collectAOISearch}=require(process.argv[1]);
let count=0,cleaned=false;
(async()=>{
  let result,error;
  try {
    result=await collectAOISearch({centers:[[100,100],[130,100]],sessionSeed:1,dwellMs:1},{
      now:()=>100,subscribe:()=>()=>{cleaned=true;},
      sendRefresh:()=>{count++;if(process.argv[2]==='failed')throw Error('query_failed');},
      sleep:async()=>{},shouldStop:()=>count>0,
    });
  }catch(e){error=e.message;}
  console.log(JSON.stringify({count,cleaned,result,error}));
})();
'''
    output = subprocess.run([node, '-e', harness, str(source), mode],
                            capture_output=True, text=True, timeout=10)
    assert output.returncode == 0, output.stderr
    result = json.loads(output.stdout)
    assert result['count'] == 1
    assert result['cleaned'] is True
    if mode == 'cancelled':
        assert result['result']['coverage_reason'] == 'search_cancelled'
        assert result['result']['complete'] is False
    else:
        assert result['error'] == 'query_failed'
