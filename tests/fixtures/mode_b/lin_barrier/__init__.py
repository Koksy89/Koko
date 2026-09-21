"""
Fixture proving that flow into eval-built code ends in a Barrier.

The variable `computed` is built dynamically and passed to eval.
Lineage must stop at the Barrier, not stitch across to what eval might do.
"""


def compute_expression():
    """Return a dynamic expression."""
    x = 10
    computed = f"x + {5}"  # Built from runtime values
    return computed


def evaluate(expr):
    """Execute dynamically built expression."""
    x = 42
    result = eval(expr)  # Lineage stops here (Barrier)
    return result


def process():
    """Main flow."""
    expr = compute_expression()
    answer = evaluate(expr)
    return answer
