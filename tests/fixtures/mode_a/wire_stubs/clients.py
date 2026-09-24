"""A target that reaches three external systems, by module name.

None of `wire_stub_blocked`, `wire_stub_record` or `wire_stub_replay` exists
on disk. That is the point: each is only importable because the owner
DECLARED it in the scenarios file and the harness installed a stub of the
kind they asked for. A module nobody declared is not importable here either,
and whatever it would have reached for is blocked at the socket layer like
anything else undeclared.

Each entry point touches exactly one client, so a test can run one kind at a
time and read what happened to it without the other two in the way.
"""


def use_blocked():
    """A declared client of kind `blocked`. The call must be stopped."""
    import wire_stub_blocked

    return wire_stub_blocked.submit_order("ACME", 100)


def use_record():
    """A declared client of kind `record`. Calls are captured; a declared
    default comes back, and nothing reaches a real system."""
    import wire_stub_record

    first = wire_stub_record.ping()
    rows = wire_stub_record.fetch_rows("select 1")
    return first, rows


def use_record_undeclared_path():
    """A path the owner's `returns` map does not name. The stub refuses to
    invent a value rather than handing back a quiet None."""
    import wire_stub_record

    return wire_stub_record.something_nobody_declared()


def use_replay():
    """A declared client of kind `replay`. Values come from the owner's
    recording, in order."""
    import wire_stub_replay

    return wire_stub_replay.get_price("ACME"), wire_stub_replay.get_price("ACME")


def use_replay_past_the_end():
    """One call more than the recording holds. A replay never loops and never
    invents a value."""
    import wire_stub_replay

    return [wire_stub_replay.get_price("ACME") for _ in range(3)]


def use_undeclared():
    """A module nobody declared. There is no stub, so there is no import."""
    import wire_stub_nobody_declared

    return wire_stub_nobody_declared.anything()
