from dataclasses import asdict
import json
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
from .transport import protocol_status
from .hunt_store import HuntStore
from .hunt import HuntCoordinator


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
        self.replenishing = False
        self.dispatching = False
        self.dispatch_idle = threading.Event()
        self.dispatch_idle.set()
        self.dispatch_coordinator = None
        self.resources_closed = False
        self.hunt_store = HuntStore(config.data_dir)
        self.hunt_coordinator = None
        if any(c['status'] == 'running' for c in self.hunt_store.list()):
            self._hunts()

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

    def _stop(self, *, pause_hunts=True):
        self.stop_event.set()
        if pause_hunts:
            for campaign in self.hunt_store.list():
                if campaign['status'] == 'running':
                    self.hunt_store.update(campaign['id'],status='paused',reason='emergency_stop:no_withdrawal')
        if self.controller:
            self.controller.stop()

    def _make_hunt_client(self):
        from .hunt_client import HuntClient
        return HuntClient(self.config, stop_event=self.stop_event)

    def _hunts(self):
        with self.lock:
            if self.hunt_coordinator is None:
                self.hunt_coordinator = HuntCoordinator(self.hunt_store,self._make_hunt_client(),
                                                        world=self.config.identity.get('world'))
                self.hunt_coordinator.start_background()
            return self.hunt_coordinator

    def _make_dispatch_client(self):
        from .hunt_dispatch_client import LiveDispatchClient
        return LiveDispatchClient(self.config, stop_event=self.stop_event)

    def _dispatches(self):
        with self.lock:
            if self.dispatch_coordinator is None:
                from .hunt_dispatch import DispatchCoordinator
                self.dispatch_coordinator = DispatchCoordinator(self.config, self._make_dispatch_client())
            return self.dispatch_coordinator

    def _listen_stop(self):
        from .windows import StopListener
        with self.lock:
            if self.closed or self.stop_event.is_set():
                raise RuntimeError('stopped')
            if self.hotkey is None:
                self.hotkey = StopListener(self._stop).start()

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
        if method == 'assess_combat':
            from ..analysis.combat_assessment import assess_combat
            return assess_combat(**params)
        if method == 'hunt_dispatch_status':
            return self._dispatches().status(**params)
        if method in {'hunt_roster_status','hunt_dispatch_start'}:
            if method == 'hunt_dispatch_start': self._validate_world(params.get('world'))
            with self.lock:
                if self.dispatching: raise RuntimeError('dispatch_busy')
                if self.replenishing: raise RuntimeError('replenish_busy')
                if self.closed or self.stop_event.is_set(): raise RuntimeError('stopped')
                if self.observing or self.job_thread and self.job_thread.is_alive(): raise RuntimeError('job_busy')
                if any(c['status']=='running' for c in self.hunt_store.list()): raise RuntimeError('hunt_active')
                self.dispatching = True
                self.dispatch_idle.clear()
            try:
                if method == 'hunt_roster_status': return self._make_dispatch_client().roster_status()
                self._listen_stop()
                return self._dispatches().dispatch(**params)
            finally:
                with self.lock:
                    self.dispatching = False
                    self.dispatch_idle.set()
        if method in {'replenish','replenish_reconcile'}:
            self._validate_world(params.get('world'))
            from .replenish import Replenisher
            from .replenish_client import ReplenishClient
            with self.lock:
                if self.dispatching: raise RuntimeError('dispatch_busy')
                if self.replenishing: raise RuntimeError('replenish_busy')
                if self.closed or self.stop_event.is_set(): raise RuntimeError('stopped')
                if self.observing or self.job_thread and self.job_thread.is_alive(): raise RuntimeError('job_busy')
                if any(c['status']=='running' for c in self.hunt_store.list()): raise RuntimeError('hunt_active')
                self.replenishing = True
            try:
                replenisher=Replenisher(self.config,client=ReplenishClient(self.config,stop_event=self.stop_event))
                return (replenisher.reconcile if method=='replenish_reconcile' else replenisher.run)(**params)
            finally:
                with self.lock: self.replenishing = False
        if method in {'hunt_start','hunt_status','hunt_stop','hunt_resume'}:
            with self.lock:
                if method in {'hunt_start','hunt_resume'} and self.dispatching: raise RuntimeError('dispatch_busy')
                if method in {'hunt_start','hunt_resume'} and self.replenishing: raise RuntimeError('replenish_busy')
                coordinator = self._hunts()
                if method == 'hunt_status': return coordinator.status(**params)
                if method == 'hunt_stop': return coordinator.stop(**params)
                if method == 'hunt_resume':
                    self.stop_event.clear()
                    return coordinator.resume(**params)
                self._validate_world(params.get('world'))
                if self.job_thread and self.job_thread.is_alive(): raise RuntimeError('job_busy')
                self.stop_event.clear()
                return coordinator.start(request_id=params['request_id'],army_ids=params.get('army_ids'))
        if method in {'prepare_login','observe_map','inspect_target','explore_region','scan_region','refresh_roster'}:
            if any(c['status'] == 'running' for c in self.hunt_store.list()):
                raise RuntimeError('hunt_active:use_hunt_status_or_stop_before_manual_acquisition')
        if method == 'protocol_status':
            return protocol_status(self.config)
        if method == "game_status":
            from .replenish_client import replenish_readiness
            replenish_reason = replenish_readiness(self.config)
            replenish_read_reason = replenish_readiness(self.config,operation='snapshot')
            return {"service": {"state": "running", "pid": os.getpid()},
                    "windows": [asdict(w) for w in self.desktop.list_windows(self.config.executable)],
                    "jobs": [self.store.get_job(params["job_id"])] if params.get("job_id") else self.store.list_jobs(),
                    "calibrated": self.config.ui.get("calibrated", False),
                    "capabilities": {
                        "managed_client": True,
                        "map_search": True, "scan_region_max_points": 2500,
                        "map_api_scan": protocol_status(self.config)['api_scan_available'],
                        "scan_region_default_source": "api", "map_ui_scan": True,
                        "bandit_recognition_calibrated": bool(self.config.ui.get('navigation', {}).get('target_detail_region')) if self.config.ui.get('navigation') else False,
                        "roster_calibrated": bool(self.config.ui.get('roster', {}).get('pages')),
                        "combat_assessment": True, "multi_army_scheduler": True,
                        "single_round_dispatch": {
                            **self._make_dispatch_client().capabilities(),
                            "active": self.dispatching,
                            "roster_tool": "sgz_hunt_roster_status",
                            "start_tool": "sgz_hunt_dispatch_start",
                            "status_tool": "sgz_hunt_dispatch_status"},
                        "replenish": {"available": replenish_reason is None, "reason": replenish_reason,
                                      "read_available": replenish_read_reason is None, "read_reason": replenish_read_reason,
                                      "reconcile_tool": "sgz_replenish_reconcile",
                                      "tool": "sgz_replenish", "transport": "api", "scope": "own_city_ordinary",
                                      "active": self.replenishing},
                        "combat": False, "hunt_execution_readiness_tool": "sgz_hunt_status"},
                    "world": self.config.identity.get('world'),
                    "identity_configured": bool(self.config.identity), "stop_requested": self.stop_event.is_set()}
        if method in {'search_map', 'query_roster'}:
            self._validate_world(params.get('world'))
            return getattr(self.store, method)(**params)
        if method == 'refresh_roster':
            self._validate_world(params.get('world'))
            if not self.config.ui.get('roster', {}).get('pages'):
                raise RuntimeError('calibration_required:roster')
            return self._start('roster', params['request_id'], {'world': params['world']})
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
                if self.dispatching: raise RuntimeError('dispatch_busy')
                if self.replenishing: raise RuntimeError('replenish_busy')
                if self.observing or self.job_thread and self.job_thread.is_alive():
                    raise RuntimeError("job_busy_use_game_status")
                self.observing = True
            try:
                if self.config.ui.get('navigation') and self._navigator().ui.matches('open_map'):
                    frame, screen = self._navigator().read_target()
                else:
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
        if method in {"explore_region", "inspect_target", "scan_region"}:
            world = params.get("world")
            self._validate_world(world)
            if method == 'scan_region':
                source = params.get('source', 'api')
                if source not in ('api', 'ui'):
                    raise ValueError('invalid_scan_source')
                if source == 'api':
                    from .client_api import readiness, validate_region
                    reason = readiness(self.config)
                    if reason:
                        raise RuntimeError('map_api_unavailable:' + reason)
                    region = {key: params[key] for key in ('x0', 'y0', 'x1', 'y1')}
                    validate_region(**region)
                    return self._start('api_scan', params['request_id'], {'world': world, 'region': region})
            if method == "inspect_target":
                points = region_points(params["x"], params["y"], params["x"], params["y"])
            else:
                points = region_points(params["x0"], params["y0"], params["x1"], params["y1"],
                                       max_points=2500 if method == 'scan_region' else 25)
            return self._start("explore", params["request_id"], {"world": world, "points": points})
        if method == "resume_task":
            job = self.store.get_job(params["job_id"])
            if job["status"] != "paused":
                raise ValueError("job_not_paused")
            return self._start(job["kind"], job["request_id"], job["payload"], resume=True)
        raise ValueError("unknown_method")

    def _validate_world(self, world):
        if not isinstance(world, str) or not world.strip():
            raise ValueError('world_required')
        if self.config.identity.get('world') and world != self.config.identity['world']:
            raise ValueError('world_mismatch')

    def _start(self, kind, request_id, payload, resume=False):
        with self.lock:
            if self.dispatching:
                raise RuntimeError('dispatch_busy')
            if self.replenishing:
                raise RuntimeError('replenish_busy')
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
            elif job['kind'] == 'roster':
                self._refresh_roster(job)
            elif job['kind'] == 'api_scan':
                self._api_scan(job)
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
        if self.config.ui.get('client_size'):
            self.desktop.prepare_window(controller.hwnd, tuple(self.config.ui['client_size']), self.stop_event)
        self.desktop.activate(controller.hwnd, stopped=self.stop_event)
        controller.park_pointer()
        flow = LoginFlow()
        while not self.stop_event.is_set():
            if self.config.ui.get('navigation'):
                navigator = self._navigator()
                if any(navigator.ui.matches(name) for name in ('open_map','settings_back','overview_back',
                        'role_back','season_back','army_list_back','city_list_back','city_interior_back')):
                    navigator.verify_identity()
                    if not navigator.ui.matches('open_map'):
                        raise RuntimeError('map_not_ready')
                    self.store.update_job(job['id'], 'complete', 'identity_verified')
                    return
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
        if not self.config.identity.get('world'):
            raise RuntimeError("identity_configuration_required")
        if job['payload']['world'] != self.config.identity['world']:
            raise RuntimeError('world_mismatch')
        navigator = self._navigator()
        navigator.ui.activate()
        navigator.verify_identity()
        completed = self.store.verified_points(job['id'])
        points = [tuple(point) for point in job['payload']['points']]
        deadline = time.monotonic() + 900
        for point in points:
            if self.stop_event.is_set():
                raise RuntimeError('stopped')
            if time.monotonic() >= deadline:
                raise RuntimeError('exploration_timeout')
            if point in completed:
                continue
            self.store.update_job(job['id'], 'running', f'inspecting:{point[0]},{point[1]}:{len(completed)}/{len(points)}')
            frame, screen = navigator.inspect(point)
            if self.stop_event.is_set():
                raise RuntimeError('stopped')
            if screen.coordinate != point:
                raise RuntimeError('coordinate_mismatch')
            evidence = self._save_frame(frame)
            self.store.save_observation(job['id'], job['payload']['world'], 'surface',
                                        *point, screen.fields, evidence)
            completed.add(point)
        self.store.update_job(job['id'], 'complete', f'verified:{len(completed)}/{len(points)}')

    def _api_scan(self, job):
        from .client_api import scan, normalize_scan
        self._listen_stop()
        self._validate_world(job['payload']['world'])
        raw = scan(self.config, job['payload']['region'], self.stop_event)
        rows = normalize_scan(raw)
        if self.stop_event.is_set():
            raise RuntimeError('stopped')
        directory = self.config.data_dir / 'evidence'
        directory.mkdir(exist_ok=True)
        evidence = directory / f"api-{uuid.uuid4().hex}.json"
        evidence.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding='utf-8')
        self.store.save_api_scan(job['id'], job['payload']['world'], rows, str(evidence))
        self.store.update_job(job['id'], 'complete',
            f"client_api:{len(rows)} tiles:{raw['elapsed_seconds']}s:bandits=personal:occupancy=unknown")

    def _refresh_roster(self, job):
        from .roster import combine_roster_pages, read_roster_page
        self._validate_world(job['payload']['world'])
        if not self.config.identity.get('world'):
            raise RuntimeError('identity_configuration_required')
        profiles = self.config.ui.get('roster', {}).get('pages')
        if not profiles:
            raise RuntimeError('calibration_required:roster')
        navigator = self._navigator()
        navigator.ui.activate()
        navigator.verify_identity()
        pages, evidence = [], []
        for profile in profiles:
            if self.stop_event.is_set():
                raise RuntimeError('stopped')
            frame, page = read_roster_page(navigator.ui, profile)
            if self.stop_event.is_set():
                raise RuntimeError('stopped')
            pages.append(page)
            evidence.append(self._save_frame(frame))
        snapshot = combine_roster_pages(pages)
        identity = self.store.save_roster(job['payload']['world'], snapshot, evidence)
        self.store.update_job(job['id'], 'complete', f"roster:{identity}:{snapshot['reason']}")

    def _navigator(self):
        from .game_ui import GameUI
        from .navigation import Navigator
        if not self.config.ui.get('calibrated') or not self.config.ui.get('navigation'):
            raise RuntimeError('calibration_required:coordinate_navigation')
        return Navigator(GameUI(self._bind(), self.vision, self.config), self.config.identity)

    def request_stop(self):
        with self.lock:
            self.closed = True
            self._stop(pause_hunts=False)
        if self.hunt_coordinator:
            self.hunt_coordinator.close()

    def close(self):
        self.request_stop()
        self.dispatch_idle.wait()
        if self.job_thread:
            self.job_thread.join()
        if self.controller:
            self.controller.close()
        if self.hotkey:
            self.hotkey.close()
        self.desktop.release()
        if not self.resources_closed:
            self.hunt_store.close()
            self.store.close()
            self.lease.close()
            self.resources_closed = True
