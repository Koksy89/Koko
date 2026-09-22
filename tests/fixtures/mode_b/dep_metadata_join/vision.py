"""The import name and the distribution name differ."""

import cv2
from sklearn.linear_model import LogisticRegression


def read(path):
    return cv2.imread(path)


def fit(features, labels):
    return LogisticRegression().fit(features, labels)
