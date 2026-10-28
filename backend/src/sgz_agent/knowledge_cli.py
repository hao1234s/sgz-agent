"""Knowledge maintenance CLI and a read-only stdio worker for DSH."""
import argparse
import json
from pathlib import Path
import sys

from .knowledge import KnowledgeBase


def emit(value, destination=None):
    destination = destination or sys.stdout
    destination.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    destination.flush()


def serve(directory, source=None, destination=None):
    source, destination = source or sys.stdin, destination or sys.stdout
    with KnowledgeBase(directory) as kb:
        for line in source:
            request = None
            try:
                if len(line) > 1_048_576:
                    raise ValueError("request_too_large")
                request = json.loads(line)
                if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or
                        not isinstance(request.get("method"), str) or not isinstance(request.get("params", {}), dict)):
                    emit({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid_request"}}, destination)
                    continue
                method, params = request["method"], request.get("params", {})
                if method == "knowledge_status" and not params:
                    result = kb.status()
                elif method == "knowledge_search":
                    result = kb.search(**params)
                else:
                    raise ValueError("unknown_method_or_parameters")
                if "id" in request:
                    emit({"jsonrpc": "2.0", "id": request["id"], "result": result}, destination)
            except Exception as error:
                if not isinstance(request, dict) or "id" in request:
                    emit({"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                          "error": {"code": -32700 if isinstance(error, json.JSONDecodeError) else -32000,
                                    "message": str(error)}}, destination)


def main():
    parser = argparse.ArgumentParser(prog="sgz-knowledge")
    parser.add_argument("command", choices=["crawl", "import", "index", "search", "status", "serve"])
    parser.add_argument("--directory", default=".state/knowledge")
    parser.add_argument("--source", help="Curated JSON pack to merge and index (required for import)")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--query")
    parser.add_argument("--kind", choices=["general", "tactic", "mechanic"])
    parser.add_argument("--season")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    try:
        if args.command == "crawl":
            from .knowledge_sources import crawl
            # Hold the same lock as the worker so maintenance cannot race queries/imports.
            with KnowledgeBase(args.directory):
                result = crawl(args.directory, args.refresh, lambda v: emit(v, sys.stderr))
            emit(result)
            return 0 if result["complete"] else 1
        if args.command == "serve":
            serve(args.directory)
            return 0
        with KnowledgeBase(args.directory) as kb:
            if args.command == "import":
                if not args.source:
                    raise ValueError("curated_source_required: use --source PATH")
                result = kb.import_curated(args.source, lambda v: emit(v, sys.stderr))
            elif args.command == "index":
                path = Path(args.directory) / "records.jsonl"
                kb.ingest(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
                result = kb.index(lambda v: emit(v, sys.stderr))
            elif args.command == "search":
                result = kb.search(args.query, kind=args.kind, season=args.season, limit=args.limit)
            else:
                result = kb.status()
            emit(result)
        return 0
    except Exception as error:
        emit({"error": str(error), "type": type(error).__name__}, sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
