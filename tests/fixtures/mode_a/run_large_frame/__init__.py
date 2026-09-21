"""A value far too large to keep whole must be SUMMARIZED, never truncated."""

PAYLOAD_LENGTH = 200000


def build_payload():
    """Return a 200,000-character ASCII string."""
    return "x" * PAYLOAD_LENGTH


def main():
    """Hand the large value across a call boundary."""
    payload = build_payload()
    return len(payload)
