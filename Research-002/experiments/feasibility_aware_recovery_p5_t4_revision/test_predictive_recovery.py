import _paths  # noqa: F401

from contact_loss_scenario_generator import _draw
from predictive_recovery_controller import CorrectiveRecoveryController
from revision_plant import PredictiveGraspEnv


def test_predictive_controller_does_not_unload_a_labeled_recovery():
    scenario = None
    seed = 4853001
    while scenario is None and seed < 4853400:
        drawn = _draw(seed, 4, "train")
        seed += 1
        if drawn is not None and drawn["oracle_protocol_a"] and drawn["event"] == "sudden_invalidation":
            scenario = drawn
    assert scenario is not None
    env = PredictiveGraspEnv(scenario, "Protocol_A")
    obs = env.reset()
    ctrl = CorrectiveRecoveryController(4, mode="fixed_margin", seed=scenario["seed"])
    ctrl.reset(obs)
    labels = []
    for _ in range(scenario["horizon_steps"]):
        command = ctrl.step(obs)
        labels.append(command["decision_label"])
        obs, info = env.step(command)
    assert info["applied_slew_violations"] == 0
    assert info["applied_bound_violations"] == 0
    decided = [label for label in labels if label is not None]
    assert decided
    assert decided[0] in ("FEASIBLE", "INFEASIBLE", "UNCERTAIN", "DEGRADED_RECOVERY")
