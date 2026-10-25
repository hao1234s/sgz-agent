import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import threading

from .config import load_config, select_window


def main():
    parser = argparse.ArgumentParser(prog="sgz-agent")
    parser.add_argument("command", choices=["doctor", "capture", "prepare-client", "serve", "inspect-transport"])
    parser.add_argument("--config", default="config/game.local.json")
    parser.add_argument("--output", default=".state/capture.png")
    parser.add_argument("--activate", action="store_true", help="Explicitly activate the selected game window")
    parser.add_argument('--input', help='Offline PCAPNG file for inspect-transport')
    parser.add_argument('--local-port', type=int, action='append', help='Game connection local port; repeatable')
    args = parser.parse_args()
    if args.command == 'inspect-transport':
        if not args.input or not args.local_port:
            parser.error('inspect-transport requires --input and --local-port')
        from .transport import summarize_capture
        print(json.dumps(summarize_capture(args.input, args.local_port), ensure_ascii=False))
        return
    config = load_config(args.config)
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
