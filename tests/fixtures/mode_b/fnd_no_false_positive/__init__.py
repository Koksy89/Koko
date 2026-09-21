"""
Fixture proving that a fully live cascade produces zero findings.

Every function is called, every branch is reachable, every feature is consumed.
The findings list must be empty.
"""


def process_input(data):
    """Transform input."""
    return data + 1


def validate_result(value):
    """Check if result is valid."""
    return value > 0


def main(raw_data):
    """Main entry point."""
    processed = process_input(raw_data)
    is_valid = validate_result(processed)
    return is_valid
