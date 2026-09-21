"""Flow into eval-built code ends at a Barrier, never stitched across."""


def build_expression(multiplier):
    """Assemble an expression from a runtime value."""
    return "value * " + str(multiplier)


def evaluate(expression, value):
    """Whatever this returns, nothing static can say it came from `value`."""
    result = eval(expression)
    return result


def process(multiplier, value):
    """The flow the slice must stop inside."""
    expression = build_expression(multiplier)
    return evaluate(expression, value)
