"""Compatibility alias for knowledge sources."""
import sys
from .knowledge import sources as _implementation
sys.modules[__name__] = _implementation
