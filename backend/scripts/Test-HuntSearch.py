"""Bounded own-army, read-only AOI search verification; never dispatches."""
import argparse
from contextlib import closing
from pathlib import Path
import json
import time

from sgz_agent.client.config import default_config_path, load_config
from sgz_agent.client.hunt_client import HuntClient
from sgz_agent.client.lease import DataLease


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-controlled-session', action='store_true')
    parser.add_argument('--army-id', required=True)
    args = parser.parse_args()
    if not args.approved_controlled_session:
        parser.error('Controlled session authorization required.')
    config = load_config(default_config_path(Path(__file__).resolve().parents[2]))
    with closing(DataLease(config.data_dir)):
        client = HuntClient(config, controlled_verification=True)
        rows = client.roster_status()['armies']
        army = next(a for a in rows if a['id'] == args.army_id)
        if army['phase'] not in {'idle','stationed'} or army['position'] is None:
            raise RuntimeError('army_not_ready_for_search')
        directory = config.data_dir / 'network-probe' / ('fresh-search-' + time.strftime('%Y%m%d-%H%M%S'))
        directory.mkdir(parents=True, exist_ok=False)
        original = client._read
        def save(operation, **params):
            result = original(operation, **params)
            (directory / (operation+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            return result
        client._read = save
        result = client.search_giants(army['position'], army_id=args.army_id)
        (directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'evidence':str(directory),'complete':result['complete'],
                          'targets':result['targets'],'native_calls':result.get('native_calls'),
                          'cleanup_verified':result.get('cleanup_verified')},ensure_ascii=True))


if __name__ == '__main__':
    main()
