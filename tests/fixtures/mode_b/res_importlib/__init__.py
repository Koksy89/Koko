"""Test importlib.import_module."""
import importlib
mod = importlib.import_module("json")
result = mod.dumps({})
