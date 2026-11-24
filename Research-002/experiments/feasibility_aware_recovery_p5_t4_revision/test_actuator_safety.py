import _paths  # noqa: F401
import numpy as np

from contact_loss_scenario_generator import _draw
from predictive_recovery_controller import CorrectiveRecoveryController
from revision_plant import PredictiveGraspEnv


def test_wild_commands_stay_inside_bounds_and_slew():
    scenario = None
    seed = 4851001
    while scenario is None and seed < 4851200:
        scenario = _draw(seed, 4, "train")
        seed += 1
    assert scenario is not None
    env = PredictiveGraspEnv(scenario, "Protocol_B")
    obs = env.reset()

    class Wild(CorrectiveRecoveryController):
        def step(self, obs):
            command = super().step(obs)
            command["cmd_fn"] = [80.0] * self.num_contacts
            return command

    ctrl = Wild(int(scenario["num_contacts"]), seed=1)
    ctrl.reset(obs)
    for _ in range(30):
        obs, info = env.step(ctrl.step(obs))
    assert info["applied_bound_violations"] == 0
    assert info["applied_slew_violations"] == 0
    assert info["applied_budget_violations"] == 0
    assert max(obs["fn_applied"]) <= 25.0 + 1e-6
    assert sum(obs["fn_applied"]) <= 8.0 * sum(obs["valid"]) + 1e-5
