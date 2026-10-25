from dataclasses import asdict
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .config import select_window
from .control import Controller
from .recognition import Recognizer
from .store import Store
from .vision import Vision
from .workflow import LoginFlow, region_points
from .lease import DataLease


class AgentService:
    def __init__(self, config, *, desktop=None):
        from .windows import WindowsDesktop
        self.config = config
        self.desktop = desktop if desktop is not None else WindowsDesktop()
        self.lease = DataLease(config.data_dir)
        try:
            self.store = Store(config.data_dir)
        except Exception:
            self.lease.close()
            raise
        self.store.recover_jobs()
        self.vision = Vision(config.data_dir / "models")
        self.recognizer = Recognizer(config.ui, config.identity)
        self.lock = threading.RLock()
        self.read_lock = threading.Lock()
        self.controller = None
        self.hotkey = None
        self.job = None
        self.job_thread = None
        self.stop_event = threading.Event()
        self.closed = False
        self.observing = False
        self.resources_closed = False

    def _bind(self):
        with self.lock:
            return self._bind_locked()

    def _bind_locked(self):
        from .windows import StopListener
        if self.closed:
            raise RuntimeError("service_closed")
        if self.controller is None:
            window = select_window(self.desktop.list_windows(self.config.executable), self.config.window_title_pattern)
            self.desktop.acquire(window.hwnd)
            self.controller = Controller(self.desktop, window.hwnd)
            try:
                self.hotkey = StopListener(self._stop).start()
            except Exception:
                self.controller = None
                self.desktop.release()
                raise
        return self.controller

    def _stop(self):
        self.stop_event.set()
        if self.controller:
            self.controller.stop()

    def _observe(self):
        with self.read_lock:
            controller = self._bind()
            frame = controller.observe()
            texts = self.vision.read(frame)
            screen = self.recognizer.classify(texts)
            return frame, screen

    def dispatch(self, method, params):
        if self.closed:
            raise RuntimeError("service_closed")
        if method == "game_status":
            return {"windows": [asdict(w) for w in self.desktop.list_windows(self.config.executable)],
                    "jobs": [self.store.get_job(params["job_id"])] if params.get("job_id") else self.store.list_jobs(),
                    "calibrated": self.config.ui.get("calibrated", False),
                    "identity_configured": bool(self.config.identity), "stop_requested": self.stop_event.is_set()}
        if method == "pause_task":
            job_id = params["job_id"]
            with self.lock:
                job = self.store.get_job(job_id)
                if job['status'] in {'complete', 'failed'}:
                    return job
                if self.job == job_id:
                    self._stop()
                return self.store.update_job(job_id, "paused", "user_paused")
        if method == "export_results":
            return {"path": str(self.store.export(params["job_id"], params.get("format", "json")))}
        if method == "observe_map":
            with self.lock:
                if self.observing or self.job_thread and self.job_thread.is_alive():
                    raise RuntimeError("job_busy_use_game_status")
                self.observing = True
            try:
                frame, screen = self._observe()
                result = asdict(screen)
                result["window"] = asdict(frame.observation.window)
                # QR/auth frames never enter the evidence archive.
                if screen.kind in {"map", "details"}:
                    result["evidence"] = self._save_frame(frame)
                return result
            finally:
                with self.lock:
                    self.observing = False
        if method == "prepare_login":
            return self._start("login", params["request_id"], {})
        if method in {"explore_region", "inspect_target"}:
            world = params.get("world")
            if not isinstance(world, str) or not world.strip():
                raise ValueError("world_required")
            if method == "inspect_target":
                points = region_points(params["x"], params["y"], params["x"], params["y"])
            else:
                points = region_points(params["x0"], params["y0"], params["x1"], params["y1"])
            return self._start("explore", params["request_id"], {"world": world, "points": points})
        if method == "resume_task":
            job = self.store.get_job(params["job_id"])
            if job["status"] != "paused":
                raise ValueError("job_not_paused")
            return self._start(job["kind"], job["request_id"], job["payload"], resume=True)
        raise ValueError("unknown_method")

    def _start(self, kind, request_id, payload, resume=False):
        with self.lock:
            if self.closed:
                raise RuntimeError("service_closed")
            if self.observing:
                raise RuntimeError("observation_busy")
            job = self.store.create_job(kind, request_id, payload)
            if resume and job['status'] != 'paused':
                raise ValueError('job_not_paused')
            if not resume and job["status"] != "pending":
                return job
            if self.job_thread and self.job_thread.is_alive():
                if self.job == job["id"]:
                    return job
                self.store.update_job(job["id"], "paused", "another_job_running")
                raise RuntimeError("another_job_running")
            self.stop_event.clear()
            if self.controller:
                self.controller.close()
                self.controller = None
            if self.hotkey:
                self.hotkey.close()
                self.hotkey = None
            self.desktop.release()
            self.job = job["id"]
            self.store.update_job(self.job, "running")
            self.job_thread = threading.Thread(target=self._run, args=(job,), daemon=True, name="sgz-job")
            self.job_thread.start()
            return self.store.get_job(self.job)

    def _run(self, job):
        try:
            if job["kind"] == "login":
                self._login(job)
            else:
                self._explore(job)
        except Exception as error:
            self.store.update_job(job["id"], "paused", str(error))
        finally:
            with self.lock:
                if self.stop_event.is_set() and self.store.get_job(job['id'])['status'] not in {'complete', 'failed'}:
                    self.store.update_job(job["id"], "paused", "stopped")

    def _login(self, job):
        if not self.desktop.list_windows(self.config.executable):
            subprocess.Popen([str(self.config.executable)], cwd=self.config.executable.parent)
            deadline = time.monotonic() + 30
            while not self.stop_event.wait(.5) and time.monotonic() < deadline:
                if self.desktop.list_windows(self.config.executable):
                    break
        controller = self._bind()
        self.desktop.activate(controller.hwnd)
        flow = LoginFlow()
        while not self.stop_event.is_set():
            frame, screen = self._observe()
            if self.stop_event.is_set():
                break
            decision = flow.next(screen)
            if decision.action in {"complete", "pause"}:
                self.store.update_job(job["id"], "complete" if decision.action == "complete" else "paused", decision.reason)
                return
            if decision.action in {"open_qr", "refresh_qr"}:
                self._click_action(decision.action, frame)
            self.store.update_job(job["id"], "waiting", decision.reason or decision.action)
            self.stop_event.wait(2)

    def _click_action(self, name, frame):
        action = self.config.ui.get("actions", {}).get(name)
        if not self.config.ui.get("calibrated") or not action:
            raise RuntimeError(f"calibration_required:{name}")
        expected = tuple(self.config.ui.get("client_size", []))
        if frame.image.size != expected:
            raise RuntimeError("calibration_size_changed")
        x, y = action["point"]
        self.controller.click(frame, round(x * expected[0]), round(y * expected[1]))

    def _save_frame(self, frame):
        directory = self.config.data_dir / "evidence"
        directory.mkdir(exist_ok=True)
        identity = uuid.uuid4().hex
        temp, final = directory / f"{identity}.tmp", directory / f"{identity}.png"
        with temp.open("wb") as stream:
            frame.image.save(stream, format="PNG")
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(final)
        return str(final)

    def _explore(self, job):
        if not self.config.identity:
            raise RuntimeError("identity_configuration_required")
        # Navigation is enabled only after its actual UI path has been calibrated.
        raise RuntimeError("calibration_required:coordinate_navigation")

    def request_stop(self):
        with self.lock:
            self.closed = True
            self._stop()

    def close(self):
        self.request_stop()
        if self.job_thread:
            self.job_thread.join()
        if self.controller:
            self.controller.close()
        if self.hotkey:
            self.hotkey.close()
        self.desktop.release()
        if not self.resources_closed:
            self.store.close()
            self.lease.close()
            self.resources_closed = True
