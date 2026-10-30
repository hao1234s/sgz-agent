"""Compatibility alias for sgz_agent.client.transport."""
import sys
from .client import transport as _implementation
sys.modules[__name__] = _implementation
