"""Compatibility alias for sgz_agent.client.workflow."""
import sys
from .client import workflow as _implementation
sys.modules[__name__] = _implementation
