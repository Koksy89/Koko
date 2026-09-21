"""Attempts name resolution by three routes. The harness must block them all.

A DNS lookup leaks the fact of the run to a resolver even when no connection
follows, so it is blocked in its own right rather than as a side effect of
connecting.
"""

import socket

MARKER_PATH = "/tmp/cascade_map_adv_dns_marker.txt"


def gethostbyname():
    """The oldest route."""
    return socket.gethostbyname("example.com")


def getaddrinfo():
    """The modern route, which the stdlib itself uses."""
    return socket.getaddrinfo("example.com", 443, proto=socket.IPPROTO_TCP)


def reverse_lookup():
    """Reverse resolution is still resolution."""
    return socket.gethostbyaddr("93.184.216.34")


def main():
    """Three lookups. None may reach a resolver."""
    results = []
    for label, action in (
        ("gethostbyname", gethostbyname),
        ("getaddrinfo", getaddrinfo),
        ("reverse_lookup", reverse_lookup),
    ):
        try:
            action()
            results.append((label, "RESOLVED"))
            with open(MARKER_PATH, "a", encoding="utf-8") as handle:
                handle.write(label + " reached a resolver\n")
        except Exception as exc:
            results.append((label, type(exc).__name__))
    return results
