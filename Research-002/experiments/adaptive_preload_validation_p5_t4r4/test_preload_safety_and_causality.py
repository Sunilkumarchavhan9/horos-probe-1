"""
HOROS PROBE-1 — Phase 5 Task 4R4
Unit, Safety, Causality, & Uncertainty Tests
File: test_preload_safety_and_causality.py
"""

import math
import numpy as np
import pytest

import _paths  # noqa: F401
from friction_probe_estimator import FrictionProbeEstimator
from friction_uncertainty_model import FrictionUncertaintyModel
from preload_force_allocator import PreloadForceAllocator
from adaptive_preload_planner import AdaptivePreloadPlanner
from preload_experiment_protocol import FN_MIN, FN_MAX, SLEW_LIMIT, PLANT_DT


def test_actuator_limits_and_slew_compliance():
    allocator = PreloadForceAllocator(fn_min=FN_MIN, fn_max=FN_MAX, slew_limit=SLEW_LIMIT, dt=PLANT_DT)
    current_fn = [5.0, 5.0, 5.0]
    target_fn = 25.0
    normals = [[0.0, 1.0], [-0.866, -0.5], [0.866, -0.5]]
    positions = [[0.0, 0.04], [-0.035, -0.02], [0.035, -0.02]]
    valid = [True, True, True]
    w_ext = [0.0, -5.0, 0.0]

    # Max delta per step is slew_limit * dt = 50 * 0.001 = 0.05 N
    cmd = allocator.allocate(current_fn, target_fn, normals, positions, valid, w_ext)
    for i in range(3):
        assert cmd[i] <= current_fn[i] + 0.05 + 1e-6
        assert cmd[i] >= FN_MIN
        assert cmd[i] <= FN_MAX


def test_budget_enforcement_protocol_b():
    allocator = PreloadForceAllocator(fn_min=FN_MIN, fn_max=FN_MAX, slew_limit=SLEW_LIMIT, dt=PLANT_DT)
    current_fn = [7.0, 7.0, 7.0]
    target_fn = 20.0
    normals = [[0.0, 1.0], [-0.866, -0.5], [0.866, -0.5]]
    positions = [[0.0, 0.04], [-0.035, -0.02], [0.035, -0.02]]
    valid = [True, True, True]
    w_ext = [0.0, -5.0, 0.0]
    budget = 3 * 8.0  # 24.0 N total

    cmd = allocator.allocate(current_fn, target_fn, normals, positions, valid, w_ext, budget=budget)
    assert sum(cmd) <= budget + 1e-5


def test_causal_isolation_no_oracle_access():
    planner = AdaptivePreloadPlanner(num_contacts=3)
    # Telemetry only carries noisy observed quantities
    noisy_obs = {
        "valid": [True, True, True],
        "fn": [4.0, 4.0, 4.0],
        "ft": [0.5, -0.2, -0.3],
        "disp": [0.00005, 0.00002, 0.00003],
        "normals": [[0.0, 1.0], [-0.866, -0.5], [0.866, -0.5]],
        "positions": [[0.0, 0.04], [-0.035, -0.02], [0.035, -0.02]],
        "w_ext_hat": [0.0, -2.0, 0.0],
    }
    act = planner.step(noisy_obs)
    assert "cmd_fn" in act
    assert len(act["cmd_fn"]) == 3
    # Verify true friction or oracle parameters are not stored in planner
    assert not hasattr(planner, "true_mu")
    assert not hasattr(planner, "oracle_feasible")


def test_friction_uncertainty_model_bounds():
    model = FrictionUncertaintyModel(coverage_z=1.96, safety_margin_N=1.5)
    # Case 1: High confidence, low uncertainty
    res1 = model.compute_safe_friction_bound(mu_hat=0.70, mu_sigma=0.05, confidence=0.90)
    assert res1["mu_safe"] < 0.70
    assert res1["mu_safe"] >= 0.50

    # Case 2: Low confidence triggers fallback
    plan_fb = model.compute_required_preload(mu_safe=0.30, f_shear_anticipated=5.0, num_contacts=3, confidence=0.20)
    assert plan_fb["is_fallback"] is True
    assert plan_fb["fn_target"] == 16.0

    # Case 3: High confidence plans physically
    plan_ok = model.compute_required_preload(mu_safe=0.50, f_shear_anticipated=6.0, num_contacts=3, confidence=0.85)
    assert plan_ok["is_fallback"] is False
    # (6.0 / (3 * 0.50)) + 1.5 = 4.0 + 1.5 = 5.5 N
    assert abs(plan_ok["fn_target"] - 5.5) < 0.1
