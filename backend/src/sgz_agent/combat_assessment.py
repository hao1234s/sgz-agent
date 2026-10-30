"""Compatibility alias for the pure assessment module."""
import sys
from .analysis import combat_assessment as _implementation
sys.modules[__name__] = _implementation
