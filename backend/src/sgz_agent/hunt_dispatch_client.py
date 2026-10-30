"""Compatibility alias for sgz_agent.client.hunt_dispatch_client."""
import sys
from .client import hunt_dispatch_client as _implementation
sys.modules[__name__] = _implementation
