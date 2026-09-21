"""A rule reachable only through a decorator-built registry: a HEURISTIC edge.

`momentum_rule` is never named at any call site. The only path from the sink to
it runs through `RULES[rule_name]`, whose key is a parameter -- so the edge that
carries it is DECORATOR_REGISTRATION/HEURISTIC. A reachability analysis that
prunes uncertain edges reports this live rule as dead code and sends the owner
to delete it.
"""

RULES = {}


def register(name):
    """Build the registry the sink dispatches through."""

    def decorate(func):
        RULES[name] = func
        return func

    return decorate


@register("momentum")
def momentum_rule(value):
    """Reachable only through RULES, never by name."""
    return value > 10


def decide(value, rule_name):
    """THE SINK."""
    rule = RULES[rule_name]
    return "enter" if rule(value) else "hold"
