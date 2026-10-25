"""Narrow client bridge for hunting. No generic RPC/Lua entry point is exposed.

Live observations and capabilities are deliberately distinct: an implemented read
does not establish that a gameplay command or a complete search was validated.
"""
import json
import math
from pathlib import Path
import threading
import time

from .client_api import _bridge_lock, readiness
from .native_session import close_native_session, retained_quarantine


READY_PHASES = {"idle", "stationed"}
VALIDATION = {
    "roster_status": True, "ready_army": True, "owned_base_positions": True,
    "direct_taofa_server_preview": True,
    "encounter_completion_identity": False, "search_coverage": False,
    "route_morale_preview": False, "base_recruit_capacity": False,
    "attack": False, "mobilize": False, "return": False,
    "recruit": False, "replenish": False,
}


def _positive_id(value):
    if isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) < 2**53:
        raise ValueError("hunt_id_invalid")
    return int(value)


def _position(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 2 or
            any(type(n) is not int or not 1 <= n <= 1620 for n in value)):
        raise ValueError("hunt_position_invalid")
    return {"row": value[0], "col": value[1]}


def preview_request(army_id, position, *, kind="attack", from_position=None):
    """Only command values recovered from the client's ARMY_CMD table.

    cmd_target_type=1 was accepted by the live regular-army TAOFA preview.
    No caller-supplied protocol name, command integer or extra data is accepted.
    """
    if kind not in {"attack", "mobilize"}:
        raise ValueError("hunt_preview_kind_invalid")
    result = {"army_id": _positive_id(army_id), "target_pos": _position(position),
              "cmd": 12 if kind == "attack" else 11, "cmd_target_type": 1}
    if from_position is not None:
        result["from_pos"] = _position(from_position)
    return result


def normalize_preview(raw, target_id, *, kind="direct", base_id=None):
    duration = raw.get("need_time")
    feasible = raw.get("result") == 1 and type(duration) in {int, float} and math.isfinite(duration) and duration >= 0
    return {"kind": kind, "base_id": base_id, "target_id": str(target_id),
            "travel_seconds": duration if feasible else None,
            "prepare_seconds": raw.get("prepare_time"), "attack_seconds": None,
            "arrival_morale": None, "path_length": raw.get("path_len"),
            "need_energy": raw.get("need_energy"), "feasible": feasible,
            "verified": False, "source": "cs_army_preview",
            "missing": ["arrival_morale", "attack_duration", "prepare_time_default_semantics"],
            "raw_preview": raw}


def prepare_protocol_command(command):
    """Build a reviewable schema-valid draft. This function NEVER sends it.

    Schema correctness does not prove gameplay semantics, availability, costs,
    ownership, readiness or server acknowledgement. Those remain execution gates.
    """
    army_id = _positive_id(command.get("army_id"))
    kind, payload = command.get("kind"), command.get("payload", {})
    missing = ["live_execution_preconditions", "command_acknowledgement_semantics"]
    if kind == "attack":
        target = payload.get("target", {})
        if target.get("use_code") != 1 or target.get("conf_id") != 10:
            raise ValueError("target_not_giant")
        _positive_id(target.get("id"))
        request = preview_request(army_id, [target.get("x"), target.get("y")])
        protocol, tag = "cs_army_cmd", 501
        missing += ["times_and_auto_fallback_semantics", "exact_target_revalidation"]
    elif kind == "mobilize":
        _positive_id(payload.get("base_id"))
        request = preview_request(army_id, payload.get("base_position"), kind="mobilize")
        protocol, tag = "cs_army_cmd", 501
        missing += ["dispatch_capacity_permission", "dispatch_target_type_semantics"]
    elif kind == "return":
        # target_type is deliberately absent: schema known, its enum not proven.
        request = {"army_id": army_id}
        protocol, tag = "cs_return_to_dispatch", 956
        missing += ["target_type_enum", "deployment_destination_revalidation"]
    elif kind == "replenish":
        heroes = payload.get("heroes", [])
        if not isinstance(heroes, list) or not 1 <= len(heroes) <= 3:
            raise ValueError("hero_list_invalid")
        ids, amounts = [], []
        for hero in heroes:
            ids.append(_positive_id(hero.get("id")))
            troops, capacity = hero.get("troops"), hero.get("capacity")
            if type(troops) is not int or type(capacity) is not int or not 0 <= troops <= capacity <= 100000:
                raise ValueError("hero_troops_invalid")
            amounts.append(capacity)
        if len(set(ids)) != len(ids):
            raise ValueError("hero_list_invalid")
        request = {"army_id": army_id, "hero_id_list": ids, "hero_troop_list": amounts}
        if "cons_index" in payload:
            index = payload["cons_index"]
            if type(index) is not int or not 0 <= index <= 10:
                raise ValueError("conscription_index_invalid")
            request["cons_index"] = index
        else:
            missing.append("conscription_index")
        protocol, tag = "cs_army_set_troops", 902
        missing += ["own_hero_membership", "city_reserve_and_queue"]
    elif kind == "recruit":
        # Server accepts one hero per operation; do not turn one journal command
        # into several unjournaled sends with potentially partial application.
        heroes = payload.get("heroes", [])
        if not isinstance(heroes, list) or len(heroes) != 1:
            raise ValueError("recruit_requires_one_journaled_hero")
        hero = heroes[0]
        capacity, troops = hero.get("capacity"), hero.get("troops")
        if type(capacity) is not int or type(troops) is not int or not 0 <= troops <= capacity <= 100000:
            raise ValueError("hero_troops_invalid")
        request = {"build_id": _positive_id(payload.get("base_id")),
                   "hero_id": _positive_id(hero.get("id")), "target_troops": capacity}
        protocol, tag = "cs_fort_conscription_begin", 1753
        missing += ["own_hero_membership", "camp_recruitment_cost_queue", "no_direct_response_confirmation"]
    else:
        raise ValueError("hunt_command_not_allowed")
    return {"request_id": command.get("request_id", command.get("id")),
            "protocol": protocol, "protocol_id": tag, "request": request,
            "schema_source": "verified_client_server_bin", "execution_verified": False,
            "sent": False, "missing": missing}


def normalize_army(raw):
    codes = set(raw.get("status_codes", []))
    phase = "unknown"
    # Win/lose are intermediate battle states, never a completion assertion.
    if 25 in codes:
        phase = "draw"
    elif codes & {24, 26}:
        phase = "fighting"
    elif raw.get("is_tying") is True:
        phase = "unknown"
    elif codes & {4, 29}:
        phase = "returning"
    elif codes & {19, 106}:
        phase = "relocating"
    elif 2 in codes:
        phase = "marching"
    elif codes == {1}:
        phase = "idle"
    elif codes in ({3}, {43}):
        phase = "stationed"
    home = str(raw["city_id"]) if raw.get("city_id") else None
    dispatch = str(raw["dispatch_build_id"]) if raw.get("dispatch_build_id") else None
    position = raw.get("position")
    if (not isinstance(position, list) or len(position) != 2 or
            any(type(v) is not int or not 1 <= v <= 1620 for v in position)):
        position = None
    return {"id": str(raw["army_id"]),
            "kind": "regular" if raw.get("army_type") == 1 else "auxiliary",
            "formed": any(type(v) is int and v > 0 for v in raw.get("hero_ids", [])),
            "home_city_id": home, "phase": phase, "position": position,
            "deployment_base_id": dispatch if dispatch != home else None,
            "current_base_id": dispatch if phase in READY_PHASES and raw.get("at_dispatch") else None,
            "target_id": str(raw["enemy_army_id"]) if raw.get("enemy_army_id") else None,
            "encounter_complete": False, "completed_target_id": None, "result": None,
            "status_reason": "tying_flag_semantics_unverified" if phase == "unknown" and raw.get("is_tying") is True else None,
            "status_codes": sorted(codes), "source": "client_lua_status",
            "observed_at": raw.get("observed_at", time.time())}


class HuntClient:
    def __init__(self, config, stop_event=None):
        self.config = config
        self.stop_event = stop_event or threading.Event()

    def capabilities(self):
        reason = readiness(self.config)
        quarantine = self._quarantine()
        missing = ([reason] if reason else []) + [k for k, verified in VALIDATION.items() if not verified]
        if quarantine:
            missing.insert(0, "hunt_runtime_quarantined:" + str(quarantine.get("incident_id", "unknown")))
        return {"available": not missing, "missing": missing, "validation": dict(VALIDATION),
                "quarantine": quarantine,
                "readonly": {"roster_status": True, "ready_army": True, "bases": True,
                             "search_giants": True}, "mutations_enabled": not missing,
                "source": "existing_client_session"}

    def roster_status(self):
        raw = self._read("roster")
        return {"complete": raw.get("complete") is True,
                "world": self.config.identity["world"],
                "armies": [normalize_army(r) for r in raw["armies"]],
                "observed_at": raw.get("observed_at", time.time()),
                "source": "client_lua_status"}

    def ready_army(self, army_id):
        army_id = str(army_id)
        status = next((a for a in self.roster_status()["armies"] if a["id"] == army_id), None)
        if not status or status["kind"] != "regular" or status["phase"] not in READY_PHASES:
            raise RuntimeError("army_not_ready_for_details")
        raw = self._read("ready", army_id=int(army_id))
        # The native bridge repeats the phase guard in the same Lua turn before
        # accessing any hero troops, stamina or morale, closing the race window.
        refreshed = normalize_army(raw["army"])
        if refreshed["phase"] not in READY_PHASES:
            raise RuntimeError("army_not_ready_for_details")
        heroes = raw.get("heroes", [])
        valid = bool(heroes) and all(type(h.get("troops")) is int and
                                    type(h.get("capacity")) is int and
                                    0 <= h["troops"] <= h["capacity"] for h in heroes)
        return {**refreshed, "heroes": heroes, "morale": raw.get("morale"),
                "stamina": raw.get("stamina"), "ready": valid and
                isinstance(raw.get("morale"), (int, float)),
                "recruitment_state_verified": False}

    def bases(self):
        return self._read("bases")["bases"]

    def search_giants(self, center, radius=70):
        if (not isinstance(center, (list, tuple)) or len(center) != 2 or
                any(type(v) is not int or not 1 <= v <= 1620 for v in center) or
                type(radius) is not int or not 1 <= radius <= 70):
            raise ValueError("hunt_search_bounds_invalid")
        raw = self._read("search", center=list(center), radius=radius)
        targets = []
        for t in raw.get("targets", []):
            if t.get("use_code") != 1 or t.get("conf_id") != 10:
                continue
            if any(type(t.get(k)) is not int for k in ("x", "y")):
                continue
            distance = math.hypot(t["x"] - center[0], t["y"] - center[1])
            if distance <= radius:
                targets.append({**t, "id": str(t["id"]), "distance": round(distance, 4)})
        return {**raw, "complete": raw.get("complete") is True,
                "targets": sorted(targets, key=lambda t: (t["distance"], t["id"])),
                "radius": radius, "center": list(center)}

    def route_options(self, army, target, bases):
        if army.get("phase") not in READY_PHASES:
            raise RuntimeError("army_not_ready_for_preview")
        request = preview_request(army["id"], [target["x"], target["y"]])
        raw = self._read("preview", request=request)
        return [normalize_preview(raw["preview"], target["id"])]

    def execute(self, command):
        quarantine = self._quarantine()
        return {"status": "rejected", "sent": False,
                "reason": "hunt_runtime_quarantined" if quarantine else "hunt_command_semantics_unverified",
                "request_id": command.get("request_id", command.get("id"))}

    def reconcile(self, command, status):
        return "unknown"

    def _quarantine(self):
        data_dir = getattr(self.config, "data_dir", None)
        if data_dir is None:
            return None
        if retained_quarantine(self.config):
            return retained_quarantine(self.config)
        path = Path(data_dir) / "hunt-client-quarantine.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {"active": True, "incident_id": "quarantine_record_unreadable"}
        if not isinstance(value, dict):
            return {"active": True, "incident_id": "quarantine_record_invalid"}
        return value if value.get("active") is not False else None

    def _read(self, operation, **params):
        if operation not in {"roster", "ready", "bases", "search", "preview"}:
            raise ValueError("hunt_operation_not_allowed")
        quarantine = self._quarantine()
        if quarantine:
            raise RuntimeError("hunt_runtime_quarantined:" + str(quarantine.get("incident_id", "unknown")))
        reason = readiness(self.config)
        if reason:
            raise RuntimeError(reason)
        if not _bridge_lock.acquire(blocking=False):
            raise RuntimeError("api_bridge_busy")
        session = script = None
        done = threading.Event()
        result, errors = {}, []
        deadline = time.monotonic() + 45
        watcher_done = threading.Event()
        cancel = watcher = None
        try:
            import frida
            cancel = frida.Cancellable()
            cancel.__enter__()
            def watch():
                while not watcher_done.wait(.05):
                    if self.stop_event.is_set() or time.monotonic() >= deadline:
                        cancel.cancel()
                        return
            watcher = threading.Thread(target=watch, daemon=True, name="hunt-client-cancel")
            watcher.start()
            if self.stop_event.is_set():
                raise RuntimeError("stopped")
            device = frida.get_local_device()
            processes = [p for p in device.enumerate_processes()
                         if p.name.lower() == self.config.executable.name.lower()]
            if len(processes) != 1:
                raise RuntimeError(f"api_client_process_count:{len(processes)}")
            identity = self.config.identity
            options = {"path": str(self.config.executable).replace("\\", "/").lower(),
                       "role_id": str(identity["role_id"]), "region": identity["region"],
                       "server_id": identity["api_server_id"], "season": identity["api_season"],
                       "operation": operation, **params}
            source = ("const OPTIONS = " + json.dumps(options) + ";\n"
                      + Path(__file__).with_name("native_lifecycle.js").read_text(encoding="utf-8") + "\n"
                      + Path(__file__).with_suffix(".js").read_text(encoding="utf-8"))
            session = device.attach(processes[0].pid)
            session.on("detached", lambda *args: (errors.append("api_client_detached"), done.set()) if not done.is_set() else None)
            script = session.create_script(source)
            def receive(msg, data):
                if msg["type"] == "error":
                    errors.append("api_script_error:" + msg.get("description", "unknown"))
                    done.set()
                elif msg["type"] == "send":
                    payload = msg["payload"]
                    if payload.get("kind") == "complete":
                        result.update(payload["data"])
                        done.set()
                    elif payload.get("kind") == "failure":
                        errors.append(payload["reason"])
                        done.set()
            script.on("message", receive)
            script.load()
            while not done.wait(.05):
                if self.stop_event.is_set():
                    raise RuntimeError("stopped")
                if time.monotonic() >= deadline:
                    raise RuntimeError("hunt_client_timeout")
            if errors:
                raise RuntimeError(errors[0])
            if self.stop_event.is_set():
                raise RuntimeError("stopped")
            return result
        finally:
            watcher_done.set()
            if watcher:
                watcher.join(1)
            if cancel:
                cancel.__exit__(None, None, None)
            try:
                close_native_session(self.config,session,script)
            finally:
                _bridge_lock.release()
