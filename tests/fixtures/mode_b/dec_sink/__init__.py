"""Decision sink."""
def get_decision(input_val):
    if input_val > 100:
        return True
    return False
FINAL_DECISION = get_decision(50)
