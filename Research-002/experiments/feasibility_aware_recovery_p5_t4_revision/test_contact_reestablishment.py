import _paths  # noqa: F401
import math

import numpy as np

from contact_loss_scenario_generator import _draw
from revision_plant import PredictiveGraspEnv


def test_finger_returns_only_by_moving():
    scenario = None
    seed = 4852001
    while scenario is None and seed < 4852200:
        scenario = _draw(seed, 4, "train")
        seed += 1
    scenario["event"] = "sudden_invalidation"
    scenario["lost_ids"] = [0]
    scenario["event_time"] = 0.0
    scenario["mu"] = [1.1, 1.1, 1.1, 1.1]
    scenario["mass"] = 0.28
    scenario["disturbance_force"] = [0.0, 0.0]
    scenario["disturbance_torque"] = 0.0
    scenario["initial_velocity"] = [0.0, 0.0]
    scenario["initial_omega"] = 0.0
    scenario["support_available"] = False
    env = PredictiveGraspEnv(scenario, "Protocol_A")
    obs = env.reset()
    # Hold a high preload so the object stays near the finger.
    for _ in range(120):
        cmd = [18.0 if v else 0.0 for v in obs["valid"]]
        obs, info = env.step({"cmd_fn": cmd, "recontact": [not v for v in obs["valid"]], "state": "RECOVERY"})
    assert info["recontact_attempts"] >= 1
    assert info["applied_slew_violations"] == 0
    assert info["recontact_successes"] >= 1 or info["displacement_m"] > 0.0
