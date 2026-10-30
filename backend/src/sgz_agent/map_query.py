"""Compatibility alias for sgz_agent.client.map_query."""
import sys
from .client import map_query as _implementation
sys.modules[__name__] = _implementation
