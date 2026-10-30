"""Compatibility alias for sgz_agent.client.hunt_runtime."""
import sys
from .client import hunt_runtime as _implementation
sys.modules[__name__] = _implementation
