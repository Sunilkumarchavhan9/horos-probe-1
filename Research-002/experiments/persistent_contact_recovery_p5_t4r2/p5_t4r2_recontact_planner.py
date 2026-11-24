"""Predict whether the finger can still reach the moving surface."""

from __future__ import annotations

from typing import Dict, List

import numpy as np

from p5_t4r2_experiment_protocol import FINGER_SPEED, RETRACT_M


def predict_contacts(obs: Dict[str, object], horizon_s: float = 0.45) -> List[Dict[str, object]]:
    velocity = np.asarray(obs["object_velocity"], dtype=float)
    position = np.asarray(obs["object_position"], dtype=float)
    reach = max(0.0, FINGER_SPEED * float(horizon_s) - RETRACT_M)
    plans = []
    for index, is_valid in enumerate(obs["valid"]):
        if is_valid:
            plans.append({"index": index, "request": False, "predicted_success": False, "reason": "active"})
            continue
        travel = float(np.linalg.norm(velocity) * float(horizon_s))
        catchable = travel <= reach + 0.005
        plans.append({
            "index": index,
            "request": bool(catchable),
            "predicted_success": bool(catchable),
            "predicted_travel_m": travel,
            "reach_m": reach,
            "object_position": [float(v) for v in position],
            "reason": "within_finger_travel" if catchable else "object_leaves_reach",
        })
    return plans
