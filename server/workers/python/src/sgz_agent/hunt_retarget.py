"""Compatibility alias for sgz_agent.client.hunt_retarget."""
import sys
from .client import hunt_retarget as _implementation
sys.modules[__name__] = _implementation
