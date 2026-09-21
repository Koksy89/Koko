"""Dataframe column lineage: assignment, assign, merge, groupby, apply."""

import pandas as pd


def engineer(trades, reference):
    """Every column written here is its own node."""
    frame = trades.copy()
    frame["spread"] = frame["ask"] - frame["bid"]
    frame = frame.assign(mid=frame["ask"] + frame["bid"])
    merged = frame.merge(reference, on="symbol")
    merged["rank"] = merged["spread"].apply(lambda value: 1 if value > 0 else 0)
    per_symbol = merged.groupby("symbol")["spread"].max()
    return merged, per_symbol
