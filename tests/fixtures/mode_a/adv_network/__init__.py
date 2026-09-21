"""Attempts a real outbound TCP connection. The harness must block it.

Nothing here runs on import: the attempts live in `main()`, which only the
harness calls, and only with the network control active.
"""

import http.client
import socket

MARKER_PATH = "/tmp/cascade_map_adv_network_marker.txt"


def raw_socket():
    """A bare outbound TCP connect to a routable address."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2)
    sock.connect(("93.184.216.34", 80))
    sock.sendall(b"GET / HTTP/1.0\r\n\r\n")
    return sock.recv(16)


def create_connection():
    """The convenience wrapper, which is a different code path."""
    return socket.create_connection(("93.184.216.34", 80), timeout=2)


def http_request():
    """A higher-level client that ends in the same syscall."""
    conn = http.client.HTTPConnection("example.com", 80, timeout=2)
    conn.request("GET", "/")
    return conn.getresponse().status


def main():
    """Three attempts. Every one must be blocked and recorded."""
    attempts = []
    for label, action in (
        ("raw_socket", raw_socket),
        ("create_connection", create_connection),
        ("http_request", http_request),
    ):
        try:
            action()
            attempts.append((label, "SUCCEEDED"))
            with open(MARKER_PATH, "a", encoding="utf-8") as handle:
                handle.write(label + " reached the network\n")
        except Exception as exc:
            attempts.append((label, type(exc).__name__))
    return attempts
