"""Compatibility alias for sgz_agent.client.lease."""
import sys
from .client import lease as _implementation
sys.modules[__name__] = _implementation
