"""The same seed and controller produce the same grasp bit twice."""

from contact_loss_scenario_generator import _draw
from run_p5_t4_experiments import _episode


def test_episode_replay_matches():
    scenario = _draw(4820100, 3, "validation")
    assert scenario is not None
    config = {"residual_mode": "none", "p_low": 0.35, "p_high": 0.8}
    task = {"scenario": scenario, "protocol": "Protocol_A", "controller": "EqualForcePerContact", "config": config}
    first = _episode(task)
    second = _episode(task)
    assert first["grasp_recovery"] == second["grasp_recovery"]
    assert abs(first["displacement_mm"] - second["displacement_mm"]) < 1e-6


def test_generator_is_deterministic():
    a = _draw(4831001, 5, "test")
    b = _draw(4831001, 5, "test")
    assert a["mu"] == b["mu"]
    assert a["oracle_protocol_a"] == b["oracle_protocol_a"]
    assert a["lost_ids"] == b["lost_ids"]
