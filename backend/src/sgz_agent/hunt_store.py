"""Compatibility alias for sgz_agent.client.hunt_store."""
import sys
from .client import hunt_store as _implementation
sys.modules[__name__] = _implementation
