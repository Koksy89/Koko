"""Target behaviour that will not reproduce, recorded as observed."""

import random
import time


def stamp():
    """Reads the wall clock: a different answer on every run."""
    return time.time()


def jitter():
    """Reads the RNG: a different answer on every run."""
    return random.random()


def main():
    """Two sources of nondeterminism, in a fixed order."""
    return stamp(), jitter()
