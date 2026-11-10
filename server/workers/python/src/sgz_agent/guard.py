"""Compatibility alias for sgz_agent.client.guard."""
import sys
from .client import guard as _implementation
sys.modules[__name__] = _implementation
