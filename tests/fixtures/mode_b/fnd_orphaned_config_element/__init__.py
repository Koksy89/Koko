"""Components are wired only by pipeline.json. One of them is never named.

`WiredStage` appears in pipeline.json. `OrphanStage` has the same shape, the
same base, and the same entry method -- and no config key, no import, no call
site and no registry anywhere names it. It is the mirror image of a dangling
reference: the element exists, the wiring does not.
"""


class Stage:
    """Base for every pipeline stage."""

    def run(self, payload):
        """Override me."""
        raise NotImplementedError


class WiredStage(Stage):
    """Named by pipeline.json."""

    def run(self, payload):
        """Do the work."""
        return payload


class OrphanStage(Stage):
    """Named by nothing."""

    def run(self, payload):
        """Do the work."""
        return payload
