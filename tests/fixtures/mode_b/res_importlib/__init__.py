"""importlib.import_module: literal resolves, computed does not."""

import importlib
import os

KNOWN = importlib.import_module("res_importlib.plugin")

CHOSEN = importlib.import_module(os.environ.get("PLUGIN", "res_importlib.plugin"))
