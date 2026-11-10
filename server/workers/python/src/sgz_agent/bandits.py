"""Compatibility alias for sgz_agent.client.bandits."""
import sys
from .client import bandits as _implementation
sys.modules[__name__] = _implementation
