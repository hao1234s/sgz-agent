import json
import sys
import threading


def serve_stream(service, source, destination):
    output_lock = threading.Lock()
    workers = []

    def reply(payload):
        with output_lock:
            destination.write(json.dumps({"jsonrpc": "2.0", **payload}, ensure_ascii=False, allow_nan=False) + "\n")
            destination.flush()

    def handle(request):
        try:
            result = service.dispatch(request["method"], request.get("params", {}))
            if "id" in request:
                reply({"id": request["id"], "result": result})
        except Exception as error:
            if "id" in request:
                reply({"id": request["id"], "error": {"code": -32000, "message": str(error)}})

    try:
        for line in source:
            if len(line) > 1_048_576:
                reply({"id": None, "error": {"code": -32600, "message": "request_too_large"}})
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                reply({"id": None, "error": {"code": -32700, "message": "parse_error"}})
                continue
            if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or
                    not isinstance(request.get("method"), str) or not isinstance(request.get("params", {}), dict)):
                reply({"id": request.get("id") if isinstance(request, dict) else None,
                       "error": {"code": -32600, "message": "invalid_request"}})
                continue
            if request["method"] in {"game_status", "pause_task"}:
                handle(request)
            else:
                workers = [t for t in workers if t.is_alive()]
                if len(workers) >= 4:
                    reply({"id": request.get("id"), "error": {"code": -32000, "message": "worker_busy"}})
                    continue
                worker = threading.Thread(target=handle, args=(request,), daemon=True)
                workers.append(worker)
                worker.start()
    finally:
        # Closing the control pipe stops input before waiting for outstanding reads.
        service.request_stop()
        for worker in workers:
            worker.join()
        service.close()


def serve(config):
    from .service import AgentService
    # Keep a private handle: OCR libraries may redirect their own noisy output.
    destination = sys.stdout
    serve_stream(AgentService(config), sys.stdin, destination)
