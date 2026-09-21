"""Uncertain edge to decision."""
def possible_path():
    return True
def make_choice(maybe_call):
    if maybe_call:
        return possible_path()
    return False
