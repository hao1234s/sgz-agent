"""Queries over observed map data; never implies coverage of unseen tiles."""
from datetime import datetime, timezone


RESOURCES = {'铁矿', '木材', '石料', '粮食'}


def integer(value, name, minimum=0, maximum=99999):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'invalid_{name}')
    return value


def bounds(x0, y0, x1, y1):
    for name, value in zip(('x0', 'y0', 'x1', 'y1'), (x0, y0, x1, y1)):
        integer(value, name)
    if x0 > x1 or y0 > y1:
        raise ValueError('invalid_region')
    return (x1-x0+1)*(y1-y0+1)


def freshness(captured_at, maximum, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        stamp = datetime.fromisoformat(captured_at)
        if stamp.tzinfo is None:
            return {'age_seconds': None, 'stale': True}
        age = (now-stamp).total_seconds()
        # A future timestamp is not trusted as a fresh observation.
        return {'age_seconds': round(max(0, age), 2), 'stale': age < -5 or age > maximum}
    except (TypeError, ValueError):
        return {'age_seconds': None, 'stale': True}


def search_rows(rows, *, world, x0, y0, x1, y1, kind='any', resource=None,
                min_level=None, max_level=None, max_age_seconds=3600,
                include_stale=False, limit=50, offset=0):
    total_tiles = bounds(x0, y0, x1, y1)
    if kind not in ('any', 'resource', 'bandit'):
        raise ValueError('invalid_kind')
    if resource is not None and (not isinstance(resource, str) or resource not in RESOURCES):
        raise ValueError('invalid_resource')
    for key, value in (('min_level', min_level), ('max_level', max_level)):
        if value is not None:
            integer(value, key, 0, 99)
    if min_level is not None and max_level is not None and min_level > max_level:
        raise ValueError('invalid_level_range')
    integer(max_age_seconds, 'max_age_seconds', 0, 31_536_000)
    integer(limit, 'limit', 1, 100)
    integer(offset, 'offset', 0, 10_000_000)
    if type(include_stale) is not bool:
        raise ValueError('invalid_include_stale')
    now = datetime.now(timezone.utc)
    found, stale_tiles, kind_known, kind_fresh_known = [], 0, 0, 0
    bandit_checked = 0
    for row in rows:
        age = freshness(row['captured_at'], max_age_seconds, now)
        stale_tiles += int(age['stale'])
        fields = row['fields']
        # OCR attempted is not a verified negative. Legacy/ambiguous bandit
        # observations remain unknown even when the coordinate was verified.
        known_resource = fields.get('category') is not None
        known_bandit = bool(fields.get('bandit'))
        known = known_bandit if kind == 'bandit' else known_resource if kind == 'resource' else known_resource or known_bandit
        kind_known += int(known)
        kind_fresh_known += int(known and not age['stale'])
        bandit_checked += int(bool(fields.get('bandit_checked')) or known_bandit)
        if age['stale'] and not include_stale:
            continue
        targets = []
        if fields.get('category') in RESOURCES:
            targets.append({'kind': 'resource', **{key: fields.get(key) for key in
                ('name', 'category', 'level', 'garrison', 'yield_per_hour')}})
        if fields.get('bandit'):
            targets.append({**fields['bandit'], 'kind': 'bandit', 'category': None})
        if not targets:
            targets.append({'kind': 'other', 'name': fields.get('name'),
                            'category': fields.get('category'), 'level': fields.get('level')})
        for target in targets:
            if kind != 'any' and target['kind'] != kind:
                continue
            if resource is not None and target.get('category') != resource:
                continue
            level = target.get('level')
            if min_level is not None and (type(level) is not int or level < min_level):
                continue
            if max_level is not None and (type(level) is not int or level > max_level):
                continue
            found.append({**{key: row[key] for key in ('world', 'layer', 'x', 'y', 'captured_at', 'evidence')},
                          **{key: fields.get(key) for key in ('source', 'resource_source', 'occupancy', 'bandit_scope')},
                          'observation_id': row['id'], **target, **age})
    return {'world': world, 'region': {'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1},
            'source': 'observations', 'queried_at': now.isoformat(),
            'results': found[offset:offset+limit], 'total': len(found),
            'next_offset': offset+limit if offset+limit < len(found) else None,
            'coverage': {'total_tiles': total_tiles, 'observed_tiles': len(rows),
                         'unobserved_tiles': total_tiles-len(rows),
                         'fresh_tiles': len(rows)-stale_tiles, 'stale_tiles': stale_tiles,
                         'kind_known_tiles': kind_known, 'kind_unknown_tiles': total_tiles-kind_known,
                         'kind_fresh_known_tiles': kind_fresh_known, 'bandit_checked_tiles': bandit_checked,
                         'world_bandits_complete': False,
                         'personal_bandit_query_tiles': sum(r['fields'].get('bandit_scope') == 'personal' for r in rows)},
            'caution': '仅查询已采集观察；未采集、过期或类型未知的点不能判定无目标。贼寇未识别不等于不存在。行动前重新读取目标。'}
