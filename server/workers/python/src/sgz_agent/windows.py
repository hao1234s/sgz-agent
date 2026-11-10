"""Compatibility alias for sgz_agent.client.windows."""
import sys
from .client import windows as _implementation
sys.modules[__name__] = _implementation
