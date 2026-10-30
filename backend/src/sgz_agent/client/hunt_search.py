"""Bounded, correlated AOI observations; no native attachment or game commands.

The captured 519/719/720/721 schemas carry a session ID but no footprint or
unique shard identity. Repeated end.num values therefore cannot prove either
transport completion or area coverage. Sampling is useful for fresh targets,
but must not authorize a scheduler requiring a complete search.
"""
from copy import deepcopy
import math


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _point(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(type(v) is not int or not 1 <= v <= 1620 for v in value)):
        raise ValueError('hunt_search_bounds_invalid')
    return tuple(value)


def _bounds(center, radius):
    center = _point(center)
    if type(radius) is not int or not 1 <= radius <= 70:
        raise ValueError('hunt_search_bounds_invalid')
    return center


def plan_query_centers(center, radius=70, *, spacing=30, max_queries=81):
    """Deterministic circular *sampling*, with no assumed AOI rectangle size."""
    cx, cy = _bounds(center, radius)
    if type(spacing) is not int or spacing < 1 or type(max_queries) is not int or not 1 <= max_queries <= 1000:
        raise ValueError('hunt_search_query_parameters_invalid')
    points = {(cx, cy)}
    offsets = {0, -radius, radius}
    for offset in range(spacing, radius, spacing):
        offsets.update((offset, -offset))
    for dx in offsets:
        for dy in offsets:
            x, y = cx + dx, cy + dy
            if 1 <= x <= 1620 and 1 <= y <= 1620 and dx*dx + dy*dy <= radius*radius:
                points.add((x, y))
                if len(points) > max_queries:
                    raise ValueError('query_budget_exceeded')
    return [list(p) for p in sorted(points, key=lambda p: ((p[0]-cx)**2+(p[1]-cy)**2, p))]


def _truncated(value):
    if isinstance(value, dict):
        return value.get('truncated') is True or any(_truncated(v) for v in value.values())
    if isinstance(value, list):
        return any(_truncated(v) for v in value)
    return False


class AOISearchAccumulator:
    """One identity-bound search, with serial, never-reused session IDs.

    Call begin only after the identity-verified bridge is about to issue 519.
    finish closes a bounded collection window, not a region coverage proof.
    Incoming epochs must be captured when decoded, never when replayed.
    """
    def __init__(self, *, world, center, radius=70, max_age=30, target_conf_id=10):
        self.center = _bounds(center, radius)
        if type(target_conf_id) is not int or target_conf_id not in (9, 10):
            raise ValueError('hunt_search_target_profile_invalid')
        if not isinstance(world, str) or not world or not _number(max_age) or not 0 < max_age <= 300:
            raise ValueError('hunt_search_identity_or_age_invalid')
        self.world, self.radius, self.max_age = world, radius, max_age
        self.target_conf_id = target_conf_id
        self._sessions, self._targets = {}, {}
        self._active = None
        self._rejected = 0

    def begin(self, *, session_id, center, sent_at):
        if type(session_id) is not int or not 1 <= session_id <= 2**53-1:
            raise ValueError('session_id_invalid')
        if session_id in self._sessions:
            raise ValueError('session_reused')
        if self._active is not None:
            raise ValueError('session_pending')
        center = _point(center)
        if not _number(sent_at) or sent_at <= 0:
            raise ValueError('session_time_invalid')
        if self._sessions and sent_at < max(s['finished_at'] for s in self._sessions.values()):
            raise ValueError('session_time_reversed')
        self._sessions[session_id] = dict(session_id=session_id, center=list(center),
            sent_at=sent_at, finished_at=None, last_event_at=sent_at,
            content_messages=0, end_markers=0, end_num_values=[], truncated=False)
        self._active = session_id

    def finish(self, *, session_id, finished_at):
        if session_id != self._active:
            raise ValueError('session_not_active')
        session = self._sessions[session_id]
        if not _number(finished_at) or finished_at < session['last_event_at']:
            raise ValueError('session_time_reversed')
        session['finished_at'] = finished_at
        session['reason'] = 'bounded_collection_window_coverage_unknown'
        self._active = None

    def ingest(self, name, data, *, received_at):
        if not isinstance(data, dict):
            self._rejected += 1
            return False
        sid = data.get('session_id')
        if (type(sid) is not int or sid != self._active or sid not in self._sessions
                or name not in {'sc_notify_refresh_aoi', 'sc_notify_refresh_aoi_content', 'sc_notify_refresh_aoi_end'}):
            self._rejected += 1
            return False
        session = self._sessions[sid]
        if not _number(received_at) or received_at < session['last_event_at']:
            self._rejected += 1
            return False
        session['last_event_at'] = received_at
        if _truncated(data):
            session['truncated'] = True
            self._rejected += 1
            return False
        if name == 'sc_notify_refresh_aoi_end':
            session['end_markers'] += 1
            num = data.get('num')
            if type(num) is int and num not in session['end_num_values']:
                session['end_num_values'].append(num)
        elif name == 'sc_notify_refresh_aoi_content':
            rows = data.get('info_list')
            if isinstance(rows, dict):
                rows = list(rows.values())
            if not isinstance(rows, list) or len(rows) > 10000:
                self._rejected += 1
                return False
            session['content_messages'] += 1
            for row in rows:
                self._update(row, sid, received_at)
        return True

    def _update(self, row, sid, received_at):
        if not isinstance(row, dict):
            return
        identity = row.get('id')
        if not ((type(identity) is int and identity > 0)
                or (isinstance(identity, str) and identity.isascii() and identity.isdigit() and int(identity) > 0)):
            return
        identity = str(identity)
        # A later deletion, reclassification, or malformed full update must not
        # leave an old giant eligible. Partial updates are conservatively omitted.
        self._targets.pop(identity, None)
        monster = row.get('monster')
        if row.get('proto_type') not in {'add', 'update', 'upd'} or not isinstance(monster, dict):
            return
        if (type(monster.get('use_code')) is not int or monster['use_code'] != 1
                or type(monster.get('conf_id')) is not int or monster['conf_id'] != self.target_conf_id):
            return
        pos = monster.get('pos')
        if not isinstance(pos, dict):
            return
        try:
            x, y = _point([pos.get('row'), pos.get('col')])
        except ValueError:
            return
        end = monster.get('end_time')
        if not _number(end) or end <= received_at:
            return
        distance_sq = (x-self.center[0])**2 + (y-self.center[1])**2
        if distance_sq > self.radius**2:
            return
        self._targets[identity] = dict(id=identity, x=x, y=y, use_code=1, conf_id=self.target_conf_id,
            end_time=end, distance=round(math.sqrt(distance_sq), 4),
            observed_at=received_at, session_id=sid, source='server_aoi_refresh')

    def result(self, *, now):
        if not _number(now) or now <= 0:
            raise ValueError('hunt_search_time_invalid')
        targets = [deepcopy(t) for t in self._targets.values()
                   if 0 <= now-t['observed_at'] <= self.max_age and t['end_time'] > now]
        targets.sort(key=lambda t: (t['distance'], t['id']))
        cx, cy = self.center
        required = sum((x-cx)**2+(y-cy)**2 <= self.radius**2
            for x in range(max(1,cx-self.radius), min(1620,cx+self.radius)+1)
            for y in range(max(1,cy-self.radius), min(1620,cy+self.radius)+1))
        return dict(world=self.world, center=list(self.center), radius=self.radius,
            complete=False, source='server_aoi_refresh', observed_at=now, targets=targets,
            coverage_reason='server_region_coverage_unverified',
            coverage=dict(required_tiles=required, verified_tiles=0,
                observed_target_tiles=len({(t['x'],t['y']) for t in targets}),
                sessions=deepcopy(list(self._sessions.values())), rejected_messages=self._rejected,
                missing_evidence=['query_footprint', 'unique_shard_identity', 'snapshot_completion_semantics']))
