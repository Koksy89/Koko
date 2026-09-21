"""After: the threshold moved by one line, and legacy_export was rewritten."""


def decide(score):
    """THE SINK. One line of this function is the whole decision."""
    if score > 20:
        return "enter"
    return "hold"


def legacy_export(rows):
    """Reached by nothing. Rewritten wholesale in the after version."""
    parts = [repr(row) for row in rows]
    prefix = "ROW"
    suffix = "END"
    joined = "|".join(parts)
    stamped = prefix + joined
    return stamped + suffix


def main(score, rows):
    """Entry point. Never calls legacy_export."""
    return decide(score)
