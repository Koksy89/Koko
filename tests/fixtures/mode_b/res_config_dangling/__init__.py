"""One real handler; the config names a different, nonexistent one."""


class RealHandler:
    """Exists, but no config key points here."""

    def handle(self, event):
        """Do the work."""
        return event
