"""Compatibility alias for sgz_agent.client.hunt_client."""
import sys
from .client import hunt_client as _implementation
sys.modules[__name__] = _implementation
