import io
import json


def test_knowledge_rpc_works_without_game_or_ocr(tmp_path):
    from sgz_agent.knowledge_cli import serve
    source = io.StringIO('\n'.join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "knowledge_status", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "knowledge_search", "params": {"query": "曹操"}}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "explore_region", "params": {}}),
        'null',
    ]))
    output = io.StringIO()
    serve(tmp_path, source, output)
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert messages[0]["result"]["records"] == 0
    assert messages[1]["result"]["results"] == []
    assert "error" in messages[2]
    assert messages[3]["error"]["code"] == -32600
