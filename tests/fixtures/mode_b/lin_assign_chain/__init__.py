"""Module with assignment chains."""


def process_data(input_value):
    """Track data through assignments."""
    x = input_value
    y = x + 1
    z = y * 2
    result = z - 1
    return result


def augmented_example(data):
    """Augmented assignment."""
    total = 0
    total += data
    total *= 2
    return total
