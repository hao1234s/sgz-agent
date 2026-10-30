"""Compatibility alias for sgz_agent.client.config."""
import sys
from .client import config as _implementation
sys.modules[__name__] = _implementation
