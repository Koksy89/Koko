"""A value named as sensitive is REDACTED at capture time, not afterwards."""


def authenticate(username, password):
    """`password` must never appear in any artifact."""
    return username + ":authenticated"


def main():
    """The literal below is the thing that must not be recorded."""
    return authenticate("alice", "hunter2-do-not-record")
