"""Compatibility alias for sgz_agent.client.vision."""
import sys
from .client import vision as _implementation
sys.modules[__name__] = _implementation
