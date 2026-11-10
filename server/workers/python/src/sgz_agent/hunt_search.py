"""Compatibility alias for sgz_agent.client.hunt_search."""
import sys
from .client import hunt_search as _implementation
sys.modules[__name__] = _implementation
