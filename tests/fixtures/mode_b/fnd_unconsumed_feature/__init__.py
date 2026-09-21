"""A feature that is computed, stored, and then read by nobody."""


def build_features(rows):
    """Two features written into the same dict."""
    features = {}
    features["momentum"] = rows[-1] - rows[0]
    features["stale_ratio"] = sum(rows) / max(len(rows), 1)
    return features


def decide(features):
    """THE SINK. Reads momentum only."""
    return "enter" if features["momentum"] > 0 else "hold"


def run(rows):
    """Entry point."""
    return decide(build_features(rows))
