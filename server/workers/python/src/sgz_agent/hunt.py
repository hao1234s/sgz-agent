"""Compatibility alias for sgz_agent.client.hunt."""
import sys
from .client import hunt as _implementation
sys.modules[__name__] = _implementation
