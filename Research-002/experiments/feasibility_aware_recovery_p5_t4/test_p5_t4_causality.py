"""The observation passed to a controller does not carry hidden plant parameters."""

import inspect

import feasibility_aware_recovery_controller as controller_module
import feasibility_estimator as estimator_module
from contact_loss_scenario_generator import _draw
from p5_t4_sim_env import FeasibilityGraspEnv


def test_observation_omits_true_friction_stiffness_and_oracle():
    scenario = _draw(4810001, 3, "train")
    assert scenario is not None
    env = FeasibilityGraspEnv(scenario, "Protocol_A")
    obs = env.reset()
    forbidden = {"mu", "mu_true", "stiffness", "oracle", "oracle_protocol_a", "stick_radius"}
    assert forbidden.isdisjoint(obs.keys())
    assert "mu_hat" in obs and "mu_sigma" in obs


def test_controller_source_does_not_read_scenario_friction():
    source = inspect.getsource(controller_module) + inspect.getsource(estimator_module)
    assert "scenario[\"mu\"]" not in source
    assert "oracle_protocol" not in source
