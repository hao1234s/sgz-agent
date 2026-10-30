"""Compatibility alias for sgz_agent.client.store."""
import sys
from .client import store as _implementation
sys.modules[__name__] = _implementation
