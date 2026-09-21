"""Branching execution order."""
def path_a():
    return 1
def path_b():
    return 2
def main(flag):
    if flag:
        return path_a()
    else:
        return path_b()
