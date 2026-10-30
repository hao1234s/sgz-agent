"""Compatibility alias for sgz_agent.client.replenish."""
import sys
from .client import replenish as _implementation
sys.modules[__name__] = _implementation
