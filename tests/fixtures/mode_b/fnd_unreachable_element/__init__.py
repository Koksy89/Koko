"""One function nothing reaches, in a package where everything else is live.

`main` is the entry point. It calls `summarize`, which calls `total`.
`legacy_report` is called by nothing: no call site, no registry, no decorator,
no config key, and no unresolved call site that could be hiding a call to it.
"""


def total(rows):
    """Called by summarize."""
    return sum(rows)


def summarize(rows):
    """Called by main."""
    return {"total": total(rows)}


def legacy_report(rows):
    """Nothing in the target reaches this. Superseded by summarize."""
    return "\n".join(str(row) for row in rows)


def main(rows):
    """Entry point."""
    return summarize(rows)
