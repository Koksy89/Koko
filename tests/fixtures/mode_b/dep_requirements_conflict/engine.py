"""Reads a frame, scores it, and returns a decision."""

import numpy as np
import pandas as pd
import requests
import yaml


def load_frame(path):
    frame = pd.DataFrame({"price": [1, 2, 3]})
    return frame.append({"price": 4}, ignore_index=True)


def score(frame):
    return np.float64(frame["price"].mean())


def decide(config_text):
    config = yaml.safe_load(config_text)
    response = requests.get(config["url"], timeout=5)
    return "BUY" if score(load_frame(response.text)) > 2 else "HOLD"
