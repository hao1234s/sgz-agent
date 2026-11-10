"""Compatibility alias for sgz_agent.client.replenish_client."""
import sys
from .client import replenish_client as _implementation
sys.modules[__name__] = _implementation
