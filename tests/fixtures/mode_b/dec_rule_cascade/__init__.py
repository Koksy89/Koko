"""An if/elif/elif/else cascade: four outcomes, three conditions."""


def classify(score, override):
    """Each condition reads `score`; the first reads `override` as well."""
    if override:
        return "forced"
    elif score < 0:
        return "reject"
    elif score < 50:
        return "review"
    else:
        return "accept"
