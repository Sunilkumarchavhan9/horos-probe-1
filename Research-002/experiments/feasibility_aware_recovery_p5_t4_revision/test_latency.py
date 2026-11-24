import _paths  # noqa: F401
import time

import numpy as np

from contact_loss_scenario_generator import _draw
from predictive_recovery_controller import RateLimitedDynamicController
from revision_plant import PredictiveGraspEnv


def test_steps_after_the_decision_stay_under_one_millisecond_at_p95():
    scenario = None
    seed = 4854001
    while scenario is None and seed < 4854200:
        scenario = _draw(seed, 4, "train")
        seed += 1
    env = PredictiveGraspEnv(scenario, "Protocol_A")
    obs = env.reset()
    ctrl = RateLimitedDynamicController(int(scenario["num_contacts"]), seed=3)
    ctrl.reset(obs)
    samples = []
    for _ in range(40):
        t0 = time.perf_counter()
        command = ctrl.step(obs)
        samples.append((time.perf_counter() - t0) * 1000.0)
        obs, info = env.step(command)
    # One classification step may be slow. The episode P95 ignores that single step.
    assert float(np.percentile(samples, 95)) < 1.0
    assert info["applied_slew_violations"] == 0
