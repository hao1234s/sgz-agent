import io
import json


class FakeService:
    def __init__(self):
        self.closed = False

    def dispatch(self, method, params):
        if method == "game_status":
            return {"state": "idle"}
        if method == "pause_task":
            return {"paused": params["job_id"]}
        raise ValueError("unknown_method")

    def close(self):
        self.closed = True

    def request_stop(self):
        pass


def test_rpc_preserves_ids_and_keeps_errors_out_of_protocol_stream():
    from sgz_agent.rpc import serve_stream
    source = io.StringIO('{"jsonrpc":"2.0","id":7,"method":"game_status","params":{}}\nnot json\n{"jsonrpc":"2.0","id":"x","method":"missing","params":{}}\n')
    out = io.StringIO()
    service = FakeService()
    serve_stream(service, source, out)
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert next(r for r in rows if r["id"] == 7)["result"] == {"state": "idle"}
    assert next(r for r in rows if r["id"] is None)["error"]["code"] == -32700
    assert "error" in next(r for r in rows if r["id"] == "x")
    assert service.closed


def test_rpc_rejects_non_object_params_before_dispatch():
    from sgz_agent.rpc import serve_stream
    out = io.StringIO()
    serve_stream(FakeService(), io.StringIO('{"jsonrpc":"2.0","id":1,"method":"game_status","params":[]}\n'), out)
    assert json.loads(out.getvalue())["error"]["code"] == -32600


def test_disconnect_stops_input_then_waits_before_closing_shared_resources():
    import threading
    from sgz_agent.rpc import serve_stream
    entered, stopped = threading.Event(), threading.Event()
    order = []
    class Service(FakeService):
        def dispatch(self, method, params):
            entered.set()
            assert stopped.wait(2), "disconnect did not stop input"
            assert not self.closed, "resources closed while request still uses them"
            order.append("request_finished")
            return {}
        def request_stop(self):
            order.append("stop")
            stopped.set()
        def close(self):
            order.append("close")
            super().close()
    def source():
        yield '{"jsonrpc":"2.0","id":1,"method":"observe_map"}\n'
        assert entered.wait(2)
    out = io.StringIO()
    serve_stream(Service(), source(), out)
    assert order == ["stop", "request_finished", "close"]
    assert "result" in json.loads(out.getvalue())


def test_pause_and_status_respond_while_ocr_is_waiting():
    import threading
    from sgz_agent.rpc import serve_stream
    entered, cancelled = threading.Event(), threading.Event()
    class Service(FakeService):
        def dispatch(self, method, params):
            if method == "observe_map":
                entered.set()
                assert cancelled.wait(2)
                return {"observed": True}
            if method == "pause_task":
                cancelled.set()
            return super().dispatch(method, params)
    def source():
        yield '{"jsonrpc":"2.0","id":1,"method":"observe_map"}\n'
        assert entered.wait(2)
        yield '{"jsonrpc":"2.0","id":2,"method":"game_status"}\n'
        yield '{"jsonrpc":"2.0","id":3,"method":"pause_task","params":{"job_id":"job"}}\n'
    out = io.StringIO()
    serve_stream(Service(), source(), out)
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert all("result" in row for row in rows)
    assert rows[0]["id"] == 2
