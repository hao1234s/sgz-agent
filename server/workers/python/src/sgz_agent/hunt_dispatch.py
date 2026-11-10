"""Compatibility alias for sgz_agent.client.hunt_dispatch."""
import sys
from .client import hunt_dispatch as _implementation
sys.modules[__name__] = _implementation
