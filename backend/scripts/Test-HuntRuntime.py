"""Controlled passive hunt runtime verification. Sends no gameplay commands."""
import argparse
from contextlib import closing
from pathlib import Path
import json
import time

from sgz_agent.client.config import default_config_path, load_config
from sgz_agent.client.hunt_runtime import HuntNativeRuntime
from sgz_agent.client.lease import DataLease


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-controlled-session',action='store_true')
    parser.add_argument('--seconds',type=int,default=600)
    parser.add_argument('--ready-army-id',help='One guarded ready-only snapshot at startup.')
    args=parser.parse_args()
    if not args.approved_controlled_session or not 1 <= args.seconds <= 1800:
        parser.error('Controlled authorization and bounded duration required.')
    config=load_config(default_config_path(Path(__file__).resolve().parents[2]))
    directory=config.data_dir/'network-probe'/('runtime-check-'+time.strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True,exist_ok=False)
    stop_file=directory/'stop'
    with closing(DataLease(config.data_dir)):
        with (directory/'events.jsonl').open('w',encoding='utf-8') as log:
            def record(event):
                log.write(json.dumps(event,ensure_ascii=True)+'\n');log.flush()
            runtime=HuntNativeRuntime(config,enabled=True,controlled_verification=True,on_event=record)
            try:
                runtime.start()
                initial=runtime.roster_status()
                (directory/'initial-roster.json').write_text(json.dumps(initial,indent=2),encoding='utf-8')
                if args.ready_army_id:
                    prepared=runtime.ready_army(args.ready_army_id)
                    (directory/'ready-army.json').write_text(json.dumps(prepared,indent=2),encoding='utf-8')
                    print(json.dumps({'prepared_army':prepared}),flush=True)
                print(json.dumps({'ready':runtime.state(),'stop_file':str(stop_file)}),flush=True)
                deadline=time.monotonic()+args.seconds
                next_poll=time.monotonic()+120
                while time.monotonic()<deadline and not stop_file.exists():
                    state=runtime.state()
                    if state['phase']!='running': raise RuntimeError(str(state))
                    if time.monotonic()>=next_poll:
                        record({'kind':'status_poll','data':runtime.roster_status()})
                        next_poll=time.monotonic()+120
                    time.sleep(.2)
            finally:
                runtime.close()
                (directory/'final.json').write_text(json.dumps(runtime.state(),indent=2),encoding='utf-8')
                print(json.dumps({'final':runtime.state(),'evidence':str(directory)}),flush=True)


if __name__=='__main__':main()
