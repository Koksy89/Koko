"""Content hashing. One function, used everywhere identity or the incremental
cache needs a stable fingerprint of bytes."""

from __future__ import annotations

import hashlib


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8", errors="surrogateescape"))
