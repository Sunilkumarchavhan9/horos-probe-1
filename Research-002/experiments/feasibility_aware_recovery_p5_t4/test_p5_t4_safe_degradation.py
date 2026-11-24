"""An impossible two-contact case must unload instead of being reported as a recovered grasp."""

import numpy as np

from feasibility_aware_recovery_controller import FeasibilityAwareRecoveryController
from p5_t4_sim_env import FeasibilityGraspEnv


def test_infeasible_pair_is_not_called_a_recovery():
    scenario = {
        "seed": 4812222,
        "split": "train",
        "num_contacts": 3,
        "angles_rad": [0.0, 2.0 * np.pi / 3.0, 4.0 * np.pi / 3.0],
        "radius": 0.04,
        "mu": [0.55, 0.53, 0.56],
        "stiffness": [4.0e4] * 3,
        "mass": 0.50,
        "inertia": 0.5 * 0.50 * 0.04 ** 2,
        "com_offset": [0.0, 0.0],
        "disturbance_force": [0.0, 0.0],
        "disturbance_torque": 0.0,
        "initial_velocity": [0.0, 0.0],
        "initial_omega": 0.0,
        "event": "sudden_invalidation",
        "event_time": 0.04,
        "event_duration": 0.05,
        "lost_ids": [0],
        "collapse_factor": 1.0,
        "stiffness_factor": 1.0,
        "support_available": True,
        "mu_noise": 0.02,
        "angle_noise_rad": 0.01,
        "horizon_steps": 140,
        "oracle_protocol_a": False,
        "n_active_after_event": 2,
    }
    env = FeasibilityGraspEnv(scenario, "Protocol_A")
    obs = env.reset()
    ctrl = FeasibilityAwareRecoveryController(3, p_low=0.35, p_high=0.8)
    ctrl.reset(obs)
    label = None
    info = None
    for _ in range(140):
        command = ctrl.step(obs)
        if command["decision_label"] is not None:
            label = command["decision_label"]
        obs, info = env.step(command)
    assert label in ("INFEASIBLE", "UNCERTAIN")
    assert not info["grasp_held"]
    assert info["escalation_events"] == 0
