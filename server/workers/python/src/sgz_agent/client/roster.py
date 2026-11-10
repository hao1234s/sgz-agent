"""Read own city/army rows only from explicitly calibrated UI regions."""
from dataclasses import asdict
import re

from .navigation import in_region
from .recognition import Recognizer, coordinate_readings, normalize


STATUSES = {'待': 'idle', '待命': 'idle', '驻守': 'stationed', '驻守中': 'stationed',
            '行军': 'marching', '行军中': 'marching', '返回': 'returning',
            '返回中': 'returning', '撤退中': 'returning', '战斗中': 'fighting',
            '征兵中': 'recruiting', '休整中': 'resting'}


def region_values(texts, region):
    if region is None:
        return []
    return [normalize(t.text) for t in texts if t.score >= .95 and in_region(t, region)]


def unique_text(texts, region):
    values = set(region_values(texts, region))
    return next(iter(values)) if len(values) == 1 else None


def coordinate(texts, region):
    values = {point for text in region_values(texts, region) for point in coordinate_readings(text)}
    if len(values) != 1 or None in values:
        return None
    return list(values.pop())


def count(texts, region):
    value = unique_text(texts, region)
    match = re.fullmatch(r'(?:城池|队伍|部队)?(?:总数|数量)?[:：]?([0-9]{1,3})(?:座|支|队)?', value or '')
    return int(match[1]) if match else None


def parse_roster_page(texts, profile):
    cities, armies, unreadable = [], [], 0
    for row in profile.get('cities', []):
        parts = [unique_text(texts, region) for region in row.get('name_regions', [row.get('name_region')])]
        name = row.get('name_separator','').join(parts) if parts and all(parts) else None
        if not name:
            unreadable += 1
            continue
        if row['kind'] not in ('main', 'sub'):
            raise ValueError('invalid_city_kind')
        counts = unique_text(texts, row.get('team_count_region'))
        counts = re.fullmatch(r'(?:队)?([0-9]{1,2})/([0-9]{1,2})', counts or '')
        cities.append({'id': row['id'], 'kind': row['kind'], 'name': name,
                       'coordinate': coordinate(texts, row.get('coordinate_region')),
                       'team_count': int(counts[1]) if counts else None,
                       'team_capacity': int(counts[2]) if counts else None})
    for row in profile.get('armies', []):
        name = unique_text(texts, row['name_region'])
        if not name:
            unreadable += 1
            continue
        status = unique_text(texts, row.get('status_region'))
        armies.append({'id': f"{row['city_id']}:{row['slot']}", 'city_id': row['city_id'],
                       'slot': row['slot'], 'name': name, 'kind':row.get('kind','regular'),
                       'coordinate': coordinate(texts, row.get('current_region')),
                       'destination': coordinate(texts, row.get('destination_region')),
                       'status': STATUSES.get(status, 'unknown'), 'status_text': status})
    return {'cities': cities, 'armies': armies, 'unreadable_rows': unreadable,
            'expected_cities': count(texts, profile.get('total_cities_region')),
            'expected_armies': count(texts, profile.get('total_armies_region')),
            'visible_text': [asdict(t) for t in texts]}


def combine_roster_pages(pages):
    def merge(key):
        items, conflicting = {}, False
        for page in pages:
            for item in page[key]:
                previous = items.get(item['id'])
                if previous is not None and previous != item:
                    conflicting = True
                items[item['id']] = item
        return list(items.values()), conflicting
    cities, city_conflict = merge('cities')
    armies, army_conflict = merge('armies')
    expected_cities = {p['expected_cities'] for p in pages if p['expected_cities'] is not None}
    expected_armies = {p['expected_armies'] for p in pages if p['expected_armies'] is not None}
    known_city_ids = {c['id'] for c in cities}
    complete = (bool(pages) and not city_conflict and not army_conflict
                and not any(p['unreadable_rows'] for p in pages)
                and expected_cities == {len(cities)} and expected_armies == {len(armies)}
                and all(a['city_id'] in known_city_ids for a in armies))
    return {'cities': cities, 'armies': armies, 'complete': complete,
            'reason': 'counts_verified' if complete else 'coverage_unverified',
            'position_complete': all(c.get('coordinate') is not None for c in cities)
                                 and all(a.get('coordinate') is not None for a in armies),
            'pages': pages}


def read_roster_page(ui, profile):
    for action in profile.get('actions', []):
        ui.act(action)
    for action in profile.get('optional_actions', []):
        if ui.matches(action):
            ui.act(action)
    for action in profile.get('after_optional_actions', []):
        ui.act(action)
    frame = ui.capture()
    anchors = profile.get('anchors', [])
    checks = profile.get('identity_checks', [])
    if not anchors or not checks:
        raise RuntimeError('calibration_required:roster_page_identity')
    texts = ui.read(frame, profile['crops'])
    good = [t for t in texts if t.score >= .95]
    if not all(Recognizer._matches(anchor, good) for anchor in anchors):
        raise RuntimeError('roster_page_mismatch')
    for check in checks:
        values = region_values(texts, check['region'])
        if values != [normalize(check['text'])]:
            raise RuntimeError('roster_identity_mismatch')
    return frame, parse_roster_page(texts, profile)
