"""Compatibility alias for sgz_agent.client.hunt_reconcile."""
import sys
from .client import hunt_reconcile as _implementation
sys.modules[__name__] = _implementation
