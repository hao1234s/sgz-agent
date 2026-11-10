"""Compatibility alias for sgz_agent.client.hunt_aftercare."""
import sys
from .client import hunt_aftercare as _implementation
sys.modules[__name__] = _implementation
