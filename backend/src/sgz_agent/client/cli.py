import argparse
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sys
import threading

from .config import default_config_path, load_config, select_window


def main():
    parser = argparse.ArgumentParser(prog="sgz-agent")
    parser.add_argument("command", choices=["doctor", "capture", "prepare-client", "serve", "inspect-transport", "replenish", "reconcile-replenish"])
    parser.add_argument("--config", default=str(default_config_path()))
    parser.add_argument("--output", default=".state/capture.png")
    parser.add_argument("--activate", action="store_true", help="Explicitly activate the selected game window")
    parser.add_argument('--input', help='Offline PCAPNG file for inspect-transport')
    parser.add_argument('--local-port', type=int, action='append', help='Game connection local port; repeatable')
    parser.add_argument('--world', help='Bound game world for replenishment')
    parser.add_argument('--request-id', help='Durable idempotency key for replenishment')
    parser.add_argument('--command-id', help='Existing unknown replenishment command to reconcile without sending')
    parser.add_argument('--army-id', action='append', help='Own army to refill; repeat or omit for all')
    parser.add_argument('--dry-run', action='store_true', help='Read eligibility only, without sending refill')
    args = parser.parse_args()
    if args.command == 'inspect-transport':
        if not args.input or not args.local_port:
            parser.error('inspect-transport requires --input and --local-port')
        from .transport import summarize_capture
        print(json.dumps(summarize_capture(args.input, args.local_port), ensure_ascii=False))
        return
    config = load_config(args.config)
    if args.command in {'replenish','reconcile-replenish'}:
        if not args.world: parser.error('replenishment requires --world')
        if args.command=='replenish' and not args.request_id: parser.error('replenish requires --request-id')
        if args.command=='reconcile-replenish' and not args.command_id: parser.error('reconciliation requires --command-id')
        from .replenish import Replenisher
        from .lease import DataLease
        with closing(DataLease(config.data_dir)):
            replenisher=Replenisher(config)
            result=(replenisher.reconcile(world=args.world,command_id=args.command_id)
                    if args.command=='reconcile-replenish' else
                    replenisher.run(world=args.world,request_id=args.request_id,
                                    army_ids=args.army_id,dry_run=args.dry_run))
        print(json.dumps(result,ensure_ascii=False))
        return
    if args.command == "serve":
        from .rpc import serve
        serve(config)
        return
    from .windows import WindowsDesktop, StopListener
    from .control import Controller
    desktop = WindowsDesktop()
    windows = desktop.list_windows(config.executable)
    if args.command == "doctor":
        hotkey = StopListener(threading.Event().set)
        try:
            hotkey.start()
            hotkey_status = "registered"
        except RuntimeError as error:
            hotkey_status = str(error)
        finally:
            hotkey.close()
        print(json.dumps({"executable_exists": config.executable.exists(), "windows": [asdict(w) for w in windows],
                          "hotkey": hotkey_status, "calibrated": config.ui.get("calibrated", False)}, ensure_ascii=False))
        return
    selected = select_window(windows, config.window_title_pattern)
    if args.command == 'prepare-client':
        desktop.acquire(selected.hwnd)
        controller = Controller(desktop, selected.hwnd)
        stopped = controller.stopped
        listener = StopListener(controller.stop)
        try:
            listener.start()
            desktop.prepare_window(selected.hwnd, tuple(config.ui['client_size']), stopped)
            desktop.activate(selected.hwnd, stopped)
            controller.park_pointer()
            if stopped.wait(.5):
                from .guard import GuardError
                raise GuardError('stopped')
            frame = controller.observe()
            if stopped.is_set():
                from .guard import GuardError
                raise GuardError('stopped')
            output = Path(args.output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            frame.image.save(output)
            print(json.dumps({'client_ready': True, 'path': str(output),
                              'window': asdict(frame.observation.window)}, ensure_ascii=False))
        finally:
            controller.close()
            listener.close()
            desktop.release()
        return
    if args.activate:
        desktop.activate(selected.hwnd)
    frame = Controller(desktop, selected.hwnd).observe()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.image.save(output)
    print(json.dumps({"path": str(output), "window": asdict(frame.observation.window)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
