"""The feature `volatility` is named twice: in this file and in features.json."""


def compute_volatility(rows):
    """Produce the feature."""
    return max(rows) - min(rows)


def build_features(rows):
    """Write the feature under the same name the config uses."""
    features = {}
    features["volatility"] = compute_volatility(rows)
    return features
