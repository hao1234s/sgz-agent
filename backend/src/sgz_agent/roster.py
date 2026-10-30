"""Compatibility alias for sgz_agent.client.roster."""
import sys
from .client import roster as _implementation
sys.modules[__name__] = _implementation
