"""Compatibility alias for sgz_agent.client.models."""
import sys
from .client import models as _implementation
sys.modules[__name__] = _implementation
