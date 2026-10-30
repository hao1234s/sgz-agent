"""Compatibility alias for sgz_agent.client.hunt_policy."""
import sys
from .client import hunt_policy as _implementation
sys.modules[__name__] = _implementation
