"""An element that definitely runs, and definitely cannot change the decision."""


def score(rows):
    """Feeds the sink."""
    return sum(rows)


def write_audit_log(rows):
    """Runs on every request. Its result is read by nothing the sink touches."""
    return {"rows_seen": len(rows)}


def final_decision(rows):
    """THE SINK."""
    return "enter" if score(rows) > 10 else "hold"


def main(rows):
    """Entry point: calls the audit, then decides."""
    write_audit_log(rows)
    return final_decision(rows)
