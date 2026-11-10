"""Compatibility alias for the client RPC entry points."""
import sys
from .client import rpc as _implementation
sys.modules[__name__] = _implementation
