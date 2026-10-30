"""Compatibility alias for sgz_agent.client.control."""
import sys
from .client import control as _implementation
sys.modules[__name__] = _implementation
