"""Compatibility entry points for the client RPC server."""
import sys
from ..core.rpc import serve_stream as _serve_stream


def serve_stream(service, source, destination):
    return _serve_stream(service, source, destination,
                         immediate_methods={'game_status', 'pause_task'})


def serve(config):
    from .service import AgentService
    serve_stream(AgentService(config), sys.stdin, sys.stdout)
