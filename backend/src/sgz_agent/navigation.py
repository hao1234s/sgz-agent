"""Compatibility alias for sgz_agent.client.navigation."""
import sys
from .client import navigation as _implementation
sys.modules[__name__] = _implementation
