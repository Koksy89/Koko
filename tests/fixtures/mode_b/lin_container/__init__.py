"""Dict keys and attributes tracked per key, not per container."""


class Settings:
    """Two attributes written independently."""

    def __init__(self):
        self.threshold = 0
        self.mode = "off"


def build(raw, label):
    """Two keys of one dict, then two attributes of one object."""
    values = {}
    values["threshold"] = raw * 2
    values["mode"] = label
    settings = Settings()
    settings.threshold = values["threshold"]
    return settings
