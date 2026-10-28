"""Explicitly authorized bounded API verification, never a UI fallback."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import time

from sgz_agent.config import load_config
from sgz_agent.replenish import Replenisher
from sgz_agent.replenish_client import ReplenishClient, save_validation, code_digest, incident_digest
from sgz_agent.lease import DataLease


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-controlled-session',action='store_true')
    parser.add_argument('--execute-army',help='At most one ordinary own-city army; omitted means read-only preview.')
    parser.add_argument('--request-id')
    parser.add_argument('--grant-validation',action='store_true')
    parser.add_argument('--grant-read-validation',action='store_true',help='Validate snapshots only; never enables troop commands.')
    args=parser.parse_args()
    if not args.approved_controlled_session:parser.error('Controlled runtime authorization required.')
    if args.execute_army and not args.request_id:parser.error('Execution requires a durable request ID.')
    if args.grant_validation and not args.execute_army:parser.error('Validation requires a verified applied command.')
    if args.grant_read_validation and args.execute_army:parser.error('Read validation requires snapshot-only verification.')
    config=load_config(Path(__file__).resolve().parents[1] / 'config/game.local.json')
    with closing(DataLease(config.data_dir)):
        verify(config,args)


def verify(config,args):
    client=ReplenishClient(config,controlled_verification=True)
    initial_code=code_digest()
    initial_incident=incident_digest(config)
    executions=[]
    original_execute=client.execute
    def execute(plan):
        result=original_execute(plan)
        executions.append(result)
        return result
    client.execute=execute
    request_id=args.request_id or ('readonly-'+time.strftime('%Y%m%d-%H%M%S'))
    if args.execute_army:
        result=Replenisher(config,client=client).run(world=config.identity['world'],
            request_id=request_id,army_ids=[args.execute_army])
    else:
        result=client.snapshot()
    directory=config.data_dir/'replenish';directory.mkdir(parents=True,exist_ok=True)
    output=directory/('verification-'+time.strftime('%Y%m%d-%H%M%S')+'.json')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'evidence':str(output),'result':result},ensure_ascii=True))
    if getattr(args,'grant_read_validation',False):
        lifecycle=result.get('native_lifecycle',{})
        if (executions or args.execute_army or result.get('complete') is not True or
                result.get('identity')!=config.identity or result.get('cleanup_verified') is not True or
                lifecycle.get('closed') is not True or
                any(type(lifecycle.get(k)) is not int or lifecycle[k]!=0 for k in ('active_native','callbacks','hooks')) or
                code_digest()!=initial_code or incident_digest(config)!=initial_incident):
            raise RuntimeError('Cannot validate an incomplete or unclean read observation.')
        save_validation(config,evidence=str(output),operation='snapshot')
        print(json.dumps({'read_validation_saved':True,'execution_validation_granted':False,'hunt_quarantine_cleared':False}))
    if args.grant_validation:
        if (len(executions)!=1 or executions[0].get('status')!='applied' or code_digest()!=initial_code or
                incident_digest(config)!=initial_incident or
                len(result.get('armies',[]))!=1 or result['armies'][0].get('status')!='applied'):
            raise RuntimeError('Cannot validate an unverified or no-op command.')
        save_validation(config,evidence=str(output))
        print(json.dumps({'scoped_validation_saved':True,'hunt_quarantine_cleared':False}))


if __name__=='__main__':main()
