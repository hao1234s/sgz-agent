"""Compatibility alias for sgz_agent.client.visual_guard."""
import sys
from .client import visual_guard as _implementation
sys.modules[__name__] = _implementation
