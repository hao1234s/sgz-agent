import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import threading

from .config import load_config, select_window


def main():
    parser = argparse.ArgumentParser(prog="sgz-agent")
    parser.add_argument("command", choices=["doctor", "capture", "serve"])
    parser.add_argument("--config", default="config/game.local.json")
    parser.add_argument("--output", default=".state/capture.png")
    parser.add_argument("--activate", action="store_true", help="Explicitly activate the selected game window")
    args = parser.parse_args()
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
    if args.activate:
        desktop.activate(selected.hwnd)
    frame = Controller(desktop, selected.hwnd).observe()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.image.save(output)
    print(json.dumps({"path": str(output), "window": asdict(frame.observation.window)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
