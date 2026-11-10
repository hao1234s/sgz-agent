"""Alias the public knowledge module so legacy monkeypatch globals stay shared."""
import sys
sys.modules[__name__] = sys.modules[__package__]
