"""A proposed intent derived from a docstring, labelled as model-written."""


def normalise(text):
    """Lowercases the text and strips surrounding whitespace."""
    return text.strip().lower()


def main():
    """normalise('  ABC  ') returns 'abc'."""
    return normalise("  ABC  ")
