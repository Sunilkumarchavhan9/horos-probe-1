"""Closed-loop recovery on one hand-specified feasible loss."""

import numpy as np

from feasibility_aware_recovery_controller import FeasibilityAwareRecoveryController
from p5_t4_sim_env import FeasibilityGraspEnv


def _easy():
    return {
        "seed": 4811111,
        "split": "train",
        "num_contacts": 4,
        "angles_rad": [0.0, np.pi / 2, np.pi, 3 * np.pi / 2],
        "radius": 0.04,
        "mu": [0.95, 0.95, 0.95, 0.95],
        "stiffness": [4.0e4] * 4,
        "mass": 0.30,
        "inertia": 0.5 * 0.30 * 0.04 ** 2,
        "com_offset": [0.0, 0.0],
        "disturbance_force": [0.2, 0.0],
        "disturbance_torque": 0.0,
        "initial_velocity": [0.0, 0.0],
        "initial_omega": 0.0,
        "event": "sudden_invalidation",
        "event_time": 0.04,
        "event_duration": 0.05,
        "lost_ids": [0],
        "collapse_factor": 1.0,
        "stiffness_factor": 1.0,
        "support_available": False,
        "mu_noise": 0.01,
        "angle_noise_rad": 0.005,
        "horizon_steps": 140,
        "oracle_protocol_a": True,
        "oracle_protocol_b": True,
        "n_active_after_event": 3,
    }


def test_easy_four_contact_loss_beats_holding_the_preload():
    scenario = _easy()
    held = FeasibilityGraspEnv(scenario, "Protocol_A")
    obs = held.reset()
    preload = list(obs["fn_applied"])
    info_hold = None
    for _ in range(140):
        obs, info_hold = held.step({"cmd_fn": preload, "recontact": [False] * 4, "state": "NORMAL"})

    env = FeasibilityGraspEnv(scenario, "Protocol_A")
    obs = env.reset()
    ctrl = FeasibilityAwareRecoveryController(4, p_low=0.2, p_high=0.6)
    ctrl.reset(obs)
    info = None
    label = None
    for _ in range(140):
        command = ctrl.step(obs)
        if command["decision_label"] is not None:
            label = command["decision_label"]
        obs, info = env.step(command)
    assert label in ("FEASIBLE", "DEGRADED_RECOVERY")
    assert info["displacement_m"] < info_hold["displacement_m"]
    assert info["applied_slew_violations"] == 0
