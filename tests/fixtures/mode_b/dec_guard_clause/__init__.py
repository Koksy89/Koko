"""Guard clauses with early return."""
def validate(data):
    if not data:
        return None
    if data < 0:
        return "invalid"
    return "ok"
