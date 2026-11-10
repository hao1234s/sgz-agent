"""Compatibility alias for sgz_agent.client.hunt_attack."""
import sys
from .client import hunt_attack as _implementation
sys.modules[__name__] = _implementation
