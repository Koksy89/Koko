"""Before: one decision threshold, and a large function nothing reaches."""


def decide(score):
    """THE SINK. One line of this function is the whole decision."""
    if score > 10:
        return "enter"
    return "hold"


def legacy_export(rows):
    """Reached by nothing. Rewritten wholesale in the after version."""
    lines = []
    for row in rows:
        lines.append(str(row))
    header = "row"
    footer = "end"
    body = ",".join(lines)
    return header + body + footer


def main(score, rows):
    """Entry point. Never calls legacy_export."""
    return decide(score)
