"""Content hashing. One function, used everywhere identity or the incremental
cache needs a stable fingerprint of bytes."""

from __future__ import annotations

import hashlib


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_hex(text.encode("utf-8", errors="surrogateescape"))


def locate_byte_offset(raw: bytes, offset: int) -> tuple[int, int]:
    """1-indexed line, 0-indexed column for a byte offset into `raw`. Used to
    point a DECODE_ERROR at the actual invalid byte rather than always
    line 1 -- `UnicodeDecodeError.start` only gives a byte offset."""
    prefix = raw[:offset]
    line = prefix.count(b"\n") + 1
    last_newline = prefix.rfind(b"\n")
    col = offset - last_newline - 1 if last_newline != -1 else offset
    return line, col
