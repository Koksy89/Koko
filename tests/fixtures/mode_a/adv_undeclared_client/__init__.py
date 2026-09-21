"""Reaching an external system nobody declared is a hard stop, not a warning.

`BrokerClient` is an external system: it sends orders. The scenario declares
no stub for it, so the harness has nothing to substitute and no way to
guarantee the order does not leave the machine. The correct outcome is that
the run stops -- not that the call is quietly allowed through, and not that
it is silently turned into a no-op the trace then reports as a successful
order.
"""

import os

MARKER_PATH = "/tmp/cascade_map_adv_undeclared_client_marker.txt"


class BrokerClient:
    """An undeclared external system."""

    endpoint = "https://broker.invalid/orders"

    def submit_order(self, symbol, quantity):
        """Would place a real order."""
        with open(MARKER_PATH, "a", encoding="utf-8") as handle:
            handle.write("ORDER {0} {1}\n".format(symbol, quantity))
        return {"status": "submitted", "symbol": symbol, "quantity": quantity}


def environment_mutation():
    """Changing the ambient environment is a side effect too."""
    os.environ["CASCADE_MAP_ADV_ENV"] = "mutated"
    return os.environ.get("CASCADE_MAP_ADV_ENV")


def main():
    """Touch the undeclared client, then mutate the environment."""
    client = BrokerClient()
    order = client.submit_order("ACME", 100)
    return order, environment_mutation()
