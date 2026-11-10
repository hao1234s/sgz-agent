"""Compatibility entry point for sgz_agent.knowledge.cli."""
import sys
from .knowledge import cli as _implementation

if __name__ == '__main__':
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
