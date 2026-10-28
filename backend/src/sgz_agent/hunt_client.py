"""Narrow client bridge for hunting. No generic RPC/Lua entry point is exposed.

Live observations and capabilities are deliberately distinct: an implemented read
does not establish that a gameplay command or a complete search was validated.
"""
import json
import math
from copy import deepcopy
from pathlib import Path
import threading
import time

from .client_api import _bridge_lock, readiness
from .native_session import close_native_session, retained_quarantine
from .hunt_search import AOISearchAccumulator, plan_query_centers

_search_sequence_lock = threading.Lock()


def reserve_search_sessions(config, count):
    """Allocate before sending; a crash consumes IDs instead of reusing them."""
    if type(count) is not int or not 1 <= count <= 81:
        raise ValueError('hunt_search_session_count_invalid')
    with _search_sequence_lock:
        path = Path(config.data_dir) / 'hunt-search-sequence.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            seed = json.loads(path.read_text(encoding='utf-8'))['next']
        except FileNotFoundError:
            seed = 1_000_000_000
        if type(seed) is not int or not 1 <= seed < 2**31-count:
            raise RuntimeError('hunt_search_session_sequence_invalid')
        temporary = path.with_suffix('.pending')
        temporary.write_text(json.dumps({'next':seed+count}), encoding='utf-8')
        temporary.replace(path)
        return seed


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


def normalize_route_preview(raw, *, origin, target):
    """Bind the proven node-count convention to the current preview's endpoints.

    The current native projection includes compressed path waypoints. Live UI
    distance and server travel times establish that path_len includes the origin
    node; waypoint count itself is not the number of traversed cells. Legacy
    offline fixtures without that projection retain an unverified estimate.
    """
    if not isinstance(raw, dict):
        raise ValueError('preview_path_invalid')
    _position(origin); _position(target)
    keys = ('actual_path_len', 'path_length_verified', 'path_length_source')
    if 'path_waypoints' not in raw:
        if any(k in raw for k in keys):
            raise ValueError('preview_path_proof_missing')
        return deepcopy(raw)
    path, nodes = raw['path_waypoints'], raw.get('path_len')
    if (not isinstance(path, list) or not 1 <= len(path) <= 4096
            or type(nodes) is not int or not 1 <= nodes < 2**53
            or type(raw.get('result')) is not int or raw['result'] != 1
            or path[0] != list(origin) or path[-1] != list(target)):
        raise ValueError('preview_path_endpoints_or_nodes_invalid')
    try:
        for point in path:
            _position(point)
    except ValueError as error:
        raise ValueError('preview_path_waypoints_invalid') from error
    # Each ordinary grid move changes each coordinate by at most one. This is
    # only a feasibility bound; the server node count supplies charged cells.
    minimum_moves = sum(max(abs(b[0]-a[0]), abs(b[1]-a[1])) for a, b in zip(path, path[1:]))
    if nodes-1 < minimum_moves:
        raise ValueError('preview_path_node_count_infeasible')
    derived = {'actual_path_len': nodes-1, 'path_length_verified': True,
               'path_length_source': 'cs_army_preview.path_nodes_minus_origin'}
    for key, expected in derived.items():
        if key in raw and (type(raw[key]) is not type(expected) or raw[key] != expected):
            raise ValueError('preview_path_derived_fields_forged')
    return {**deepcopy(raw), **derived}


def normalize_preview(raw, target_id, *, kind="direct", base_id=None):
    # Scheduler fields split deployment, base preparation and final attack march.
    # None of them denotes combat duration. A direct route has no deployment leg.
    duration = raw.get("need_time")
    feasible = raw.get("result") == 1 and type(duration) in {int, float} and math.isfinite(duration) and duration >= 0
    return {"kind": kind, "base_id": base_id, "target_id": str(target_id),
            "travel_seconds": 0 if kind=="direct" else None,
            "prepare_seconds": 0 if kind=="direct" else None,
            "attack_seconds": duration if feasible else None,
            "command_prepare_seconds": raw.get("prepare_time"),
            "arrival_morale": None, "path_length": raw.get("path_len"),
            "need_energy": raw.get("need_energy"), "feasible": feasible,
            "verified": False, "source": "cs_army_preview",
            "missing": ["arrival_morale", "command_prepare_semantics"],
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


def _arrived_dispatch(raw):
    position, dispatch = raw.get('position'), raw.get('dispatch_position')
    entered, observed = raw.get('dispatch_entered_at'), raw.get('observed_at')
    return (isinstance(position, list) and len(position) == 2
            and all(type(v) is int and 1 <= v <= 1620 for v in position)
            and dispatch == position
            and type(raw.get('dispatch_build_id')) is int and raw['dispatch_build_id'] > 0
            and type(entered) in (int, float) and math.isfinite(entered) and entered > 0
            and type(observed) in (int, float) and math.isfinite(observed) and entered <= observed
            and type(raw.get('movement_end_time')) in (int, float) and raw['movement_end_time'] == 0
            and type(raw.get('movement_total_time')) in (int, float) and raw['movement_total_time'] == 0)


def normalize_army(raw):
    codes = set(raw.get("status_codes", []))
    coherent = (len(codes) == 1 and type(raw.get('army_status')) is int
                and type(raw.get('system_status')) is int
                and raw['army_status'] == raw['system_status'] == next(iter(codes)))
    confirmed = (len(codes) == 1 and next(iter(codes)) in (1,3,19,43)
                 and raw.get('army_status') == next(iter(codes))
                 and raw.get('system_status') == next(iter(codes))
                 and raw.get('movement_end_time') == 0 and raw.get('movement_total_time') == 0)
    phase = "unknown"
    # Win/lose are intermediate battle states, never a completion assertion.
    if 25 in codes:
        phase = "draw"
    elif codes & {24, 26}:
        phase = "fighting"
    elif (raw.get("is_tying") is True or 'army_status' in raw or 'system_status' in raw) and not (
            confirmed or coherent and codes <= {2,4,19,29,106}):
        phase = "unknown"
    elif codes & {4, 29}:
        phase = "returning"
    elif codes == {19} and _arrived_dispatch(raw):
        phase = "stationed"
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
            # An opponent army is a defender, not the map EntityInfo.id used by
            # target reservations. No validated join exists between these IDs.
            "target_id": None,
            "enemy_army_id": str(raw["enemy_army_id"]) if raw.get("enemy_army_id") else None,
            "encounter_complete": False, "completed_target_id": None, "result": None,
            "status_reason": "tying_flag_semantics_unverified" if phase == "unknown" and raw.get("is_tying") is True else None,
            "status_codes": sorted(codes), "source": "client_lua_status",
            "observed_at": raw.get("observed_at", time.time())}


class HuntClient:
    def __init__(self, config, stop_event=None, *, controlled_verification=False):
        self.config = config
        self.stop_event = stop_event or threading.Event()
        self.controlled_verification = controlled_verification
        self.last_diagnostic = None

    def capabilities(self):
        reason = readiness(self.config)
        quarantine = self._quarantine()
        missing = ([reason] if reason else []) + [k for k, verified in VALIDATION.items() if not verified]
        if quarantine:
            missing.insert(0, "hunt_runtime_quarantined:" + str(quarantine.get("incident_id", "unknown")))
        if getattr(self.config, 'data_dir', None):
            from .hunt_attack import pending_attack
            if pending_attack(self.config): missing.append('controlled_attack_pending')
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

    def search_giants(self, center, radius=70, *, army_id=None):
        if (not isinstance(center, (list, tuple)) or len(center) != 2 or
                any(type(v) is not int or not 1 <= v <= 1620 for v in center) or
                type(radius) is not int or not 1 <= radius <= 70):
            raise ValueError("hunt_search_bounds_invalid")
        if army_id is not None:
            army_id = _positive_id(army_id)
            centers = plan_query_centers(center, radius)
            seed = reserve_search_sessions(self.config, len(centers))
            raw = self._read('search_fresh', army_id=army_id, center=list(center), radius=radius,
                             centers=centers, session_seed=seed, dwell_ms=1000, timeout_ms=60000)
            accumulator = AOISearchAccumulator(world=self.config.identity['world'], center=center,
                                               radius=radius, max_age=120)
            for session in raw.get('sessions', []):
                accumulator.begin(session_id=session['session_id'], center=session['center'],
                                  sent_at=session['sent_at'])
                for event in raw.get('events', []):
                    if event['data'].get('session_id') == session['session_id']:
                        accumulator.ingest(event['name'], event['data'], received_at=event['received_at'])
                accumulator.finish(session_id=session['session_id'], finished_at=session['finished_at'])
            return {**accumulator.result(now=time.time()), 'metadata':raw.get('metadata'),
                    'dispatches':raw.get('dispatches', []), 'native_calls':raw.get('native_calls'),
                    'cleanup_verified':raw.get('cleanup_verified')}
        raw = self._read("search", center=list(center), radius=radius)
        targets = []
        for t in raw.get("targets", []):
            if ('end_time' in t and (type(t['end_time']) not in (int,float) or
                    not math.isfinite(t['end_time']) or t['end_time'] <= time.time())):
                continue
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
        if command.get('kind') == 'replenish':
            from .replenish import Replenisher, HuntReplenishmentBlocked
            from .replenish_client import ReplenishClient
            client = ReplenishClient(self.config, stop_event=self.stop_event,
                                     controlled_verification=self.controlled_verification)
            # This gate performs no native work. Only this known preflight failure
            # may be returned as unsent; arbitrary execution exceptions stay unknown.
            try:
                client.ensure_execution_ready()
            except RuntimeError as error:
                return {'status':'rejected','sent':False,'reason':str(error)}
            try:
                outcome = Replenisher(self.config,client=client).run_for_hunt(
                    hunt_id=command['hunt_id'],command_id=command['id'])
            except HuntReplenishmentBlocked as error:
                return {'status':'rejected','sent':False,'reason':str(error)}
            return self._replenishment_receipt(command,outcome)
        quarantine = self._quarantine()
        return {"status": "rejected", "sent": False,
                "reason": "hunt_runtime_quarantined" if quarantine else "hunt_command_semantics_unverified",
                "request_id": command.get("request_id", command.get("id"))}

    def reconcile(self, command, status):
        if command.get('kind') == 'replenish':
            from .replenish import Replenisher
            outcome = Replenisher(self.config).cached_for_hunt(
                hunt_id=command['hunt_id'],command_id=command['id'])
            if outcome:
                receipt = self._replenishment_receipt(command,outcome)
                if receipt['status'] in {'applied','not_applied'}: return receipt['status']
                if receipt['status'] == 'rejected' and receipt.get('sent') is False: return 'not_applied'
        return "unknown"

    @staticmethod
    def _replenishment_receipt(command, outcome):
        rows = outcome.get('armies', [])
        if len(rows) != 1 or rows[0].get('army_id') != command['army_id']:
            return {'status':'unknown','reason':'replenishment_result_identity_mismatch'}
        result = rows[0]
        receipt = {**result,'replenish_command_id':result.get('command_id'),
                   'replenish_request_id':outcome.get('request_id')}
        if result.get('status') in {'applied','already_full'}:
            return {**receipt,'status':'applied'}
        if result.get('sent') is False and result.get('status') in {'blocked','rejected'}:
            retryable = result.get('reason') in {'reserve_insufficient','wounded_recovery_pending',
                                                 'recruitment_state_unverified'}
            return {**receipt,'status':'not_applied' if retryable else 'rejected','retryable':retryable}
        return {**receipt,'status':'unknown'}

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
        if operation not in {"roster", "ready", "bases", "search", "search_fresh", "preview"}:
            raise ValueError("hunt_operation_not_allowed")
        quarantine = self._quarantine()
        if quarantine and not self.controlled_verification:
            raise RuntimeError("hunt_runtime_quarantined:" + str(quarantine.get("incident_id", "unknown")))
        if self.controlled_verification:
            from .replenish_client import replenish_readiness
            reason = replenish_readiness(self.config, controlled_verification=True, operation='snapshot')
        else:
            reason = readiness(self.config)
        if reason:
            raise RuntimeError(reason)
        if not _bridge_lock.acquire(blocking=False):
            raise RuntimeError("api_bridge_busy")
        session = script = None
        done = threading.Event()
        result, errors = {}, []
        deadline = time.monotonic() + (75 if operation == 'search_fresh' else 45)
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
            bridge_source = Path(__file__).with_suffix('.js').read_text(encoding='utf-8')
            if operation == 'search_fresh':
                options['operation'] = 'search'
                marker = 'let busy=false, finished=false;'
                if bridge_source.count(marker) != 1: raise RuntimeError('hunt_helper_boundary_changed')
                bridge_source = bridge_source.split(marker)[0] + '\n' + '\n'.join(
                    Path(__file__).with_name(name).read_text(encoding='utf-8')
                    for name in ('hunt_search.js', 'hunt_search_bridge.js'))
            source = ("const OPTIONS = " + json.dumps(options) + ";\n"
                      + Path(__file__).with_name("native_lifecycle.js").read_text(encoding="utf-8") + "\n"
                      + bridge_source)
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
                        result['native_lifecycle'] = payload.get('lifecycle')
                        done.set()
                    elif payload.get("kind") == "failure":
                        self.last_diagnostic = payload
                        errors.append(payload["reason"])
                        done.set()
                    elif payload.get('kind') == 'search_diagnostic':
                        self.last_diagnostic = payload
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
                if result and not errors:
                    result['cleanup_verified'] = True
            finally:
                _bridge_lock.release()
