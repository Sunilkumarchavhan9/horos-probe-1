"""Predict whether a finger can catch the contact before the recovery window closes."""

from __future__ import annotations

from typing import Dict, List

import numpy as np

FINGER_SPEED = 0.18
RETRACT_M = 0.010
HORIZON_S = 0.40


def predict_contacts(obs: Dict[str, object]) -> List[Dict[str, object]]:
    velocity = np.asarray(obs["object_velocity"], dtype=float)
    position = np.asarray(obs["object_position"], dtype=float)
    plans = []
    reach = max(0.0, FINGER_SPEED * HORIZON_S - RETRACT_M)
    for i, is_valid in enumerate(obs["valid"]):
        if is_valid:
            plans.append({"index": i, "request": False, "predicted_success": False, "reason": "still_valid"})
            continue
        normal = np.asarray(obs["normal"][i], dtype=float)
        local = np.asarray(obs["position"][i], dtype=float)
        outward = -normal
        future_point = position + local + velocity * HORIZON_S
        now_point = position + local
        gap = float(np.linalg.norm(future_point - now_point))
        closing = float(np.dot(velocity, outward))
        catchable = gap <= reach and closing < FINGER_SPEED
        plans.append({
            "index": i,
            "request": bool(catchable),
            "predicted_success": bool(catchable),
            "predicted_gap_m": gap,
            "reach_m": reach,
            "reason": "within_finger_travel" if catchable else "object_leaves_finger_reach",
        })
    return plans
