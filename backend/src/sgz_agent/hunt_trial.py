"""Compatibility alias for sgz_agent.client.hunt_trial."""
import sys
from .client import hunt_trial as _implementation

if __name__ == '__main__':
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
