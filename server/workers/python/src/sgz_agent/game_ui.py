"""Compatibility alias for sgz_agent.client.game_ui."""
import sys
from .client import game_ui as _implementation
sys.modules[__name__] = _implementation
