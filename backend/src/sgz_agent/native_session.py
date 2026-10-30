"""Compatibility alias for sgz_agent.client.native_session."""
import sys
from .client import native_session as _implementation
sys.modules[__name__] = _implementation
