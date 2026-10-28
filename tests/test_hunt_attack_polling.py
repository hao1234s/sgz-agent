"""A fake clock measures early status confirmation without replaying a send."""
from copy import deepcopy
from contextlib import closing
import json
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest


class Clock:
    def __init__(self):
        self.elapsed = 0.0
        self.after_sleep = None

    def monotonic(self):
        return self.elapsed

    def time(self):
        return 100 + self.elapsed

    def sleep(self, seconds):
        assert 0 <= seconds <= .05 + 1e-9
        self.elapsed += seconds
        if self.after_sleep:
            self.after_sleep()


def run_attack(tmp_path, monkeypatch, *, ready_at=.5, patch=None, cleanup_failure=False,
               read_failure=False, stop_at=None, fault_at=None, passive=False):
    from test_hunt_attack import plan
    from sgz_agent import hunt_attack as m
    clock = Clock(); stop = Event()
    if stop_at is not None:
        clock.after_sleep = lambda: stop.set() if clock.elapsed >= stop_at else None
    monkeypatch.setattr(m.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(m.time, 'time', clock.time)
    monkeypatch.setattr(m.time, 'sleep', clock.sleep)
    config = SimpleNamespace(data_dir=tmp_path, identity={'world':'w'})
    sent, polls, budgets, durable_during_native = [], [], [], []
    p = plan()

    def row():
        value = dict(army_id=364067, army_type=1, hero_ids=[1,2,3],
            status_codes=[2], army_status=2, system_status=2, command=12, auto_return=True,
            destination=[587,1550], position=[558,1527], dispatch_build_id=11183,
            movement_total_time=300, movement_end_time=401, observed_at=clock.time(),
            enemy_army_id=None, fight_position=None)
        if clock.elapsed < ready_at:
            value.update(status_codes=[19], army_status=19, system_status=19, command=None,
                         movement_total_time=0, movement_end_time=0, destination=None)
        value.update(patch or {})
        return value

    def persisted():
        with closing(sqlite3.connect(tmp_path/'hunt-attacks.sqlite3')) as db:
            value = db.execute('SELECT status,result FROM attacks').fetchone()
        durable_during_native.append(value)

    class Runtime:
        request_timeout = 15

        def __init__(self, *args, **kwargs):
            self.generation='polling'; self.phase='new'; self.cleanup_fault=None
            self.events=SimpleNamespace(events=self.events_after_send)

        def events_after_send(self, **kwargs):
            if passive and sent:
                q=m.attack_request(p)
                return [dict(sequence=1,event=dict(name='cs_army_cmd',data=q)),
                    dict(sequence=2,event=dict(name='sc_sync_army_status',data=dict(army_info=dict(
                        army_id=364067,status=dict(cmd=12,status=[2],end_pos=q['target_pos'],
                            is_auto_fallback=True,total_time=300,end_time=401)))))]
            if clock.elapsed >= .28:
                return [dict(sequence=1,event=dict(name='cs_army_cmd.response',data={'result':True},epoch=100.28))]
            return []

        def start(self):
            self.phase='running'

        def request(self, operation):
            assert operation == 'attack', 'status polling must not request troop/hero details'
            sent.append(clock.elapsed)
            return dict(status='submitted', sent=True)

        def roster_status(self):
            persisted()
            polls.append(clock.elapsed); budgets.append(self.request_timeout)
            if read_failure:
                raise RuntimeError('status_read_failed')
            return dict(complete=True, armies=[row()], observed_at=clock.time())

        def close(self):
            persisted()
            assert self.request_timeout == 15, 'temporary status budget leaked into runtime cleanup'
            self.phase='closed'
            if cleanup_failure:
                self.cleanup_fault='detach_unconfirmed'; self.phase='quarantined'
                raise RuntimeError('detach_unconfirmed')

        def state(self):
            fault='heartbeat_lost' if fault_at is not None and clock.elapsed >= fault_at else None
            return dict(generation=self.generation, phase=self.phase, fault=fault,
                        cleanup_fault=self.cleanup_fault, bridge_retained=self.phase=='quarantined')

    result=m.execute_under_lease(config,request_id='early-status',plan=p,runtime_factory=Runtime,stop_event=stop)
    with m.AttackJournal(tmp_path) as journal:
        pending=deepcopy(journal.pending('w'))
        replay=m.execute_under_lease(config,request_id='early-status',plan=p,runtime_factory=Runtime,stop_event=stop)
    assert replay == result
    assert sent == [0], 'an acknowledgement/status read must never replay the attack'
    assert all(state=='submitted' and receipt is None for state,receipt in durable_during_native)
    return result, polls, clock.elapsed, budgets, pending


def test_departure_ready_at_three_quarters_second_finishes_before_two_fake_seconds(tmp_path,monkeypatch):
    result,polls,elapsed,budgets,pending=run_attack(tmp_path,monkeypatch,ready_at=.5)
    assert result['status']=='accepted' and result['departure_verified'] is True
    assert elapsed < 2
    assert polls == pytest.approx([.25,.75])
    assert result['departure_poll_count']==2
    assert result['untagged_ack_response_after_claim_seconds']==pytest.approx(.28)
    assert result['status_observed_after_claim_seconds']==pytest.approx(.75)
    assert result['confirmation_elapsed_seconds']==pytest.approx(.75)
    assert all(0 < budget <= 1 for budget in budgets)
    assert len(pending)==1 and pending[0]['status']=='accepted'
    assert pending[0]['result']==result


@pytest.mark.parametrize('patch', [dict(destination=[588,1550]), dict(hero_ids=[2,1,3]),
    dict(position=[559,1527],hero_ids=[2,1,3]),
    dict(movement_end_time=420), dict(army_id=364068), dict(auto_return=False),
    dict(observed_at=80)])
def test_wrong_status_never_shortens_wait_into_false_acceptance(tmp_path,monkeypatch,patch):
    result,polls,elapsed,_,pending=run_attack(tmp_path,monkeypatch,ready_at=0,patch=patch)
    assert result['status']=='unknown' and result['sent'] is True
    assert elapsed == pytest.approx(15)
    assert polls == pytest.approx([.25,.75,1.5,3,5,10,15])
    assert pending[0]['status']=='unknown'


def test_delayed_matching_status_uses_bounded_schedule_without_native_write_retry(tmp_path,monkeypatch):
    result,polls,elapsed,_,_=run_attack(tmp_path,monkeypatch,ready_at=4)
    assert result['status']=='accepted'
    assert polls == pytest.approx([.25,.75,1.5,3,5])
    assert elapsed == pytest.approx(5)


def test_early_witness_with_cleanup_failure_remains_unknown(tmp_path,monkeypatch):
    result,polls,elapsed,_,pending=run_attack(tmp_path,monkeypatch,cleanup_failure=True)
    assert result['status']=='unknown' and result['sent'] is True
    assert elapsed < 2 and len(polls)==2
    assert pending[0]['status']=='unknown'


@pytest.mark.parametrize('reason',['stop','fault'])
def test_stop_or_fault_before_first_poll_performs_no_native_status_read(tmp_path,monkeypatch,reason):
    options={'stop_at':.2} if reason=='stop' else {'fault_at':.2}
    result,polls,_,_,pending=run_attack(tmp_path,monkeypatch,**options)
    assert polls==[]
    assert result['status']=='unknown' and result['sent'] is True
    assert pending[0]['status']=='unknown'


def test_status_read_error_stops_remaining_polls_and_preserves_fence(tmp_path,monkeypatch):
    result,polls,elapsed,_,pending=run_attack(tmp_path,monkeypatch,read_failure=True)
    assert polls == pytest.approx([.25])
    assert elapsed < 1
    assert result['status']=='unknown' and result['sent'] is True
    assert result['departure_read_error']=='status_read_failed'
    assert pending[0]['status']=='unknown'


def test_complete_matching_packet_remains_an_immediate_fast_path(tmp_path,monkeypatch):
    result,polls,elapsed,_,pending=run_attack(tmp_path,monkeypatch,passive=True)
    assert result['status']=='accepted' and result['departure_verified'] is True
    assert polls==[] and elapsed==0
    assert pending[0]['status']=='accepted'
