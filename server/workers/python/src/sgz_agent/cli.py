"""Compatibility alias for sgz_agent.client.cli."""
import sys
from .client import cli as _implementation

if __name__ == '__main__':
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
