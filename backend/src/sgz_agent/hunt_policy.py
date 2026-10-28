"""Explicit discovery bounds; coordinate radius never represents march cost."""
import json
from pathlib import Path


_TARGETS = {
    10: {'use_code': 1, 'conf_id': 10, 'display_name': '巨戟士', 'defender_count': 3},
    9: {'use_code': 1, 'conf_id': 9, 'display_name': '重徒兵', 'defender_count': 2},
}


def target_profile(config, *, world=None):
    """Only the two fixed ordinary-NPC profiles; nine needs explicit permission."""
    directory = getattr(config, 'data_dir', None)
    if directory is None:
        return dict(_TARGETS[10])
    try:
        record = json.loads((Path(directory) / 'hunt-user-policy.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return dict(_TARGETS[10])
    except (OSError, ValueError) as error:
        raise ValueError('hunt_target_policy_invalid') from error
    if not isinstance(record, dict):
        raise ValueError('hunt_target_policy_invalid')
    expected = world if world is not None else getattr(config, 'identity', {}).get('world')
    if record.get('world') != expected or 'target_conf_id' not in record:
        return dict(_TARGETS[10])
    conf_id = record['target_conf_id']
    if type(conf_id) is not int or conf_id not in _TARGETS:
        raise ValueError('hunt_target_policy_invalid')
    authorization = record.get('target_authorization')
    if conf_id == 9 and (not isinstance(expected, str) or not expected.strip()
                        or not isinstance(authorization, str) or not authorization.strip()):
        raise ValueError('hunt_target_authorization_required')
    return dict(_TARGETS[conf_id])


def search_radius(config, *, world=None):
    directory = getattr(config, 'data_dir', None)
    if directory is None:
        return 70
    try:
        record = json.loads((Path(directory) / 'hunt-user-policy.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return 70
    except (OSError, ValueError) as error:
        raise ValueError('hunt_search_policy_invalid') from error
    if not isinstance(record, dict):
        raise ValueError('hunt_search_policy_invalid')
    expected = world if world is not None else getattr(config, 'identity', {}).get('world')
    if record.get('world') != expected or 'search_radius' not in record:
        return 70
    radius = record['search_radius']
    if type(radius) is not int or radius not in (70, 140):
        raise ValueError('hunt_search_policy_invalid')
    authorization = record.get('search_radius_authorization')
    if radius == 140 and (not isinstance(expected, str) or not expected.strip()
                         or not isinstance(authorization, str) or not authorization.strip()):
        raise ValueError('hunt_search_authorization_required')
    return radius
