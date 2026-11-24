"""Applied forces stay inside the slew limit and the 25 N bound."""

import numpy as np

from p5_t4_sim_env import FN_MAX, SLEW, FeasibilityGraspEnv


def _scenario():
    return {
        "seed": 4813333,
        "num_contacts": 3,
        "angles_rad": [0.0, 2.1, 4.2],
        "radius": 0.04,
        "mu": [0.8, 0.8, 0.8],
        "stiffness": [3.0e4] * 3,
        "mass": 0.3,
        "inertia": 2.4e-4,
        "com_offset": [0.0, 0.0],
        "disturbance_force": [0.0, 0.0],
        "disturbance_torque": 0.0,
        "initial_velocity": [0.0, 0.0],
        "initial_omega": 0.0,
        "event": "sudden_invalidation",
        "event_time": 1.0,
        "event_duration": 0.01,
        "lost_ids": [],
        "collapse_factor": 1.0,
        "stiffness_factor": 1.0,
        "support_available": False,
        "mu_noise": 0.01,
        "angle_noise_rad": 0.0,
        "horizon_steps": 30,
    }


def test_wild_commands_are_clipped_and_slew_limited():
    env = FeasibilityGraspEnv(_scenario(), "Protocol_A")
    env.reset()
    previous = env.fn.copy()
    info = None
    for _ in range(30):
        obs, info = env.step({"cmd_fn": [100.0, -20.0, 80.0], "recontact": [False, False, False], "state": "NORMAL"})
        delta = np.max(np.abs(env.fn - previous))
        assert delta <= SLEW * env.dt + 1e-6
        assert np.max(env.fn) <= FN_MAX + 1e-6
        assert np.min(env.fn) >= -1e-8
        previous = env.fn.copy()
    assert info["applied_bound_violations"] == 0
    assert info["applied_slew_violations"] == 0


def test_protocol_b_budget_is_enforced_on_the_applied_force():
    env = FeasibilityGraspEnv(_scenario(), "Protocol_B")
    env.reset()
    for _ in range(20):
        env.step({"cmd_fn": [25.0, 25.0, 25.0], "recontact": [False] * 3, "state": "NORMAL"})
        assert float(np.sum(env.fn)) <= 3 * 8.0 + 1e-4
