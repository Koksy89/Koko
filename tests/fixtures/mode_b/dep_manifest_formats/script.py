# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx>=0.27",
# ]
# ///
"""A PEP 723 single-file script."""

import httpx


def fetch(url):
    return httpx.get(url)
