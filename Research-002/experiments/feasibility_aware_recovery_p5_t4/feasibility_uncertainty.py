"""Uncertainty summaries for a Bernoulli feasibility probability."""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence

import numpy as np


def wilson_interval(successes: int, n: int, z: float = 1.96) -> List[float]:
    if n <= 0:
        return [0.0, 1.0]
    p = successes / float(n)
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n) / denom
    return [float(max(0.0, center - half)), float(min(1.0, center + half))]


def expected_calibration_error(probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 8) -> dict:
    if not probabilities:
        return {"ece": None, "bins": []}
    probs = np.asarray(probabilities, dtype=float)
    ys = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = len(probs)
    ece = 0.0
    rows = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        if i == bins - 1:
            mask = (probs >= lo) & (probs <= hi)
        else:
            mask = (probs >= lo) & (probs < hi)
        count = int(np.sum(mask))
        if count == 0:
            continue
        conf = float(np.mean(probs[mask]))
        acc = float(np.mean(ys[mask]))
        ece += (count / total) * abs(acc - conf)
        rows.append({"bin_low": float(lo), "bin_high": float(hi), "count": count, "confidence": conf, "frequency": acc})
    return {"ece": float(ece), "bins": rows}


def brier_score(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
    if not probabilities:
        return float("nan")
    p = np.asarray(probabilities, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    return float(np.mean((p - y) ** 2))
