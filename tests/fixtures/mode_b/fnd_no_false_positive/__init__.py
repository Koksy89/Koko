"""A fully live cascade. Every finding kind must stay silent.

Every function is called, every branch depends on a value, every feature is
read by the decision, every name is defined once, no two bodies are alike, and
nothing is wired by config. Zero findings is the only correct answer.
"""


def process_input(data):
    """Called by main."""
    return data + 1


def validate_result(value):
    """Called by main; its result is the decision."""
    return value > 0


def main(raw_data):
    """Entry point."""
    processed = process_input(raw_data)
    is_valid = validate_result(processed)
    return is_valid
