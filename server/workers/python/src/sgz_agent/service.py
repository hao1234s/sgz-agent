"""Compatibility alias for sgz_agent.client.service."""
import sys
from .client import service as _implementation
sys.modules[__name__] = _implementation
