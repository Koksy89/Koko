"""Dataframe operations."""
import pandas as pd
def transform(df):
    df["new_col"] = df["old_col"] * 2
    return df
