"""Compatibility alias for sgz_agent.client.recognition."""
import sys
from .client import recognition as _implementation
sys.modules[__name__] = _implementation
