"""Compatibility alias for sgz_agent.client.client_api."""
import sys
from .client import client_api as _implementation
sys.modules[__name__] = _implementation
