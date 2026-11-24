"""
HOROS PROBE-1 — Phase 5 Task 4R4
Controlled Preload Sweep & Benchmark Runner
File: preload_sweep_experiment.py

Executes:
1. Controlled initial preload sweep (3, 5, 8, 12, 16, 20, 25 N).
2. Pre-contact friction probing and estimation evaluation.
3. 10-controller held-out benchmark evaluation on 600 scenarios.
4. Paired McNemar statistics, mechanical work, damage index, and safety checks.
"""

from __future__ import annotations

import _paths  # noqa: F401
import json
import math
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
from scipy.stats import binomtest

from contact_topology import ContactTopology, ContactPoint
from n_contact_sim_env import NContactGraspEnvP5T3
from preload_controller_baselines import make_controller, get_initial_preload
from preload_experiment_protocol import (
    SWEEP_TARGETS,
    FRICTION_LEVELS,
    TOPOLOGIES,
    PROTOCOL_A,
    PROTOCOL_B,
    PLANT_DT,
    SLEW_LIMIT,
    FN_MIN,
    FN_MAX,
    BUDGET_PER_CONTACT,
)

HERE = Path(__file__).resolve().parent

DISTURBANCE_REGIMES = [
    "shear_ramp",
    "fast_ramp",
    "tangential_shock",
    "micro_probe",
    "normal_perturbation",
    "torque_pulse",
    "unequal_friction",
    "unequal_stiffness",
    "off_center_load",
    "mass_inertia_variation",
]


def generate_scenario(seed: int, split: str = "test") -> Dict[str, Any]:
    """Generates a fully reproducible, matched grasping scenario."""
    rng = np.random.RandomState(seed)

    # Topology: 3, 4, or 5 contacts
    num_contacts = int(rng.choice(TOPOLOGIES))
    base_mu = float(rng.choice(FRICTION_LEVELS))
    disturbance = str(rng.choice(DISTURBANCE_REGIMES))

    # Object parameters
    mass = float(np.clip(rng.normal(0.50, 0.08), 0.30, 0.80)) if disturbance == "mass_inertia_variation" else 0.50
    radius = 0.040

    # Per-contact variations
    contacts_config = []
    d_phi = 2.0 * math.pi / float(num_contacts)
    for i in range(num_contacts):
        phi = float(i) * d_phi
        mu_i = base_mu
        if disturbance == "unequal_friction":
            mu_i = float(np.clip(base_mu + rng.uniform(-0.15, 0.15), 0.20, 0.90))

        e_star_i = 1.589e6
        g_star_i = 2.667e5
        if disturbance == "unequal_stiffness":
            scale = rng.uniform(0.80, 1.20)
            e_star_i *= scale
            g_star_i *= scale

        contacts_config.append({
            "contact_id": i,
            "angle_rad": phi,
            "pos_rel": [radius * math.cos(phi), radius * math.sin(phi)],
            "normal": [-math.cos(phi), -math.sin(phi)],
            "tangent": [-math.sin(phi), math.cos(phi)],
            "mu_static": mu_i,
            "mu_kinetic": 0.85 * mu_i,
            "e_star": e_star_i,
            "g_star": g_star_i,
        })

    # Disturbance parameters
    v_shear = 0.0025
    load_offset_y = 0.0
    if disturbance == "fast_ramp":
        v_shear = 0.0080
    elif disturbance == "off_center_load":
        load_offset_y = 0.015

    return {
        "seed": int(seed),
        "split": split,
        "num_contacts": num_contacts,
        "base_mu": base_mu,
        "disturbance": disturbance,
        "object_mass": mass,
        "object_radius": radius,
        "v_shear": v_shear,
        "load_offset_y": load_offset_y,
        "contacts": contacts_config,
    }


def make_env_from_scenario(scenario: Dict[str, Any], seed: int) -> NContactGraspEnvP5T3:
    """Instantiates NContactGraspEnvP5T3 matching scenario parameters."""
    cps = []
    for c in scenario["contacts"]:
        cp = ContactPoint(
            contact_id=c["contact_id"],
            pos_rel=np.asarray(c["pos_rel"], dtype=float),
            normal=np.asarray(c["normal"], dtype=float),
            tangent=np.asarray(c["tangent"], dtype=float),
            fingertip_radius=0.015,
            mu_static=c["mu_static"],
            mu_kinetic=c["mu_kinetic"],
            youngs_modulus_star=c["e_star"],
            shear_modulus_star=c["g_star"],
            is_active=True,
        )
        cps.append(cp)

    topology = ContactTopology(
        contacts=cps,
        object_mass=scenario["object_mass"],
        object_radius=scenario["object_radius"],
    )

    env = NContactGraspEnvP5T3(topology=topology, dt=PLANT_DT, seed=seed)
    return env


def run_single_episode(task: Dict[str, Any]) -> Dict[str, Any]:
    """Runs a single simulation episode under matched scenario and controller."""
    scenario = task["scenario"]
    controller_name = task["controller"]
    protocol = task["protocol"]
    init_fn = float(task.get("initial_preload", get_initial_preload(controller_name, protocol)))
    seed = int(scenario["seed"])

    env = make_env_from_scenario(scenario, seed=seed)
    n_contacts = scenario["num_contacts"]
    ctrl = make_controller(controller_name, n_contacts, seed=seed, initial_preload=init_fn)

    # Initial preloads
    initial_preloads = [init_fn] * n_contacts
    obs = env.reset(
        disturbance_type=scenario["disturbance"],
        v_shear=scenario["v_shear"],
        load_offset_y=scenario["load_offset_y"],
        initial_preloads=initial_preloads,
    )

    budget = (n_contacts * BUDGET_PER_CONTACT) if protocol == PROTOCOL_B else None

    latencies = []
    slew_violations = 0
    bound_violations = 0
    budget_violations = 0
    prev_cmd = list(env.fn)

    estimation_info = {}
    selected_preload = init_fn

    for step_i in range(500):
        # Format causal obs for controller
        contacts_obs = obs["contacts"]
        fn_noisy = [c["force_n"] for c in contacts_obs]
        ft_noisy = [c["force_t"] for c in contacts_obs]
        disp_noisy = [c["disp"] for c in contacts_obs]
        valid_flags = [c["is_active"] for c in contacts_obs]

        # Estimated external wrench from contact force balance: W_ext_hat = - G * F
        G = env.topology.get_grasp_matrix(obs["object_tilt"])
        forces_vec = np.zeros(2 * n_contacts)
        for i in range(n_contacts):
            forces_vec[2 * i] = fn_noisy[i]
            forces_vec[2 * i + 1] = ft_noisy[i]
        w_ext_hat = (-G @ forces_vec).tolist()

        ctrl_obs = {
            "valid": valid_flags,
            "fn": fn_noisy,
            "ft": ft_noisy,
            "disp": disp_noisy,
            "normals": [list(cp.normal) for cp in env.topology.contacts],
            "positions": [list(cp.pos_rel) for cp in env.topology.contacts],
            "w_ext_hat": w_ext_hat,
            "protocol_budget": budget,
        }

        t0 = time.perf_counter()
        act = ctrl.step(ctrl_obs)
        latencies.append((time.perf_counter() - t0) * 1000.0)

        cmd_fn = act["cmd_fn"]
        if "selected_preload" in act:
            selected_preload = float(act["selected_preload"])
        if "planning_info" in act and act["planning_info"]:
            estimation_info = act["planning_info"]

        # Check safety violations on command
        for i, f_val in enumerate(cmd_fn):
            if f_val < FN_MIN - 1e-4 or f_val > FN_MAX + 1e-4:
                bound_violations += 1
            if abs(f_val - prev_cmd[i]) > (SLEW_LIMIT * PLANT_DT) + 1e-4:
                slew_violations += 1
        if budget is not None and sum(cmd_fn) > budget + 1e-4:
            budget_violations += 1
        prev_cmd = list(cmd_fn)

        obs, done, info = env.step(cmd_fn)
        if done:
            break

    # Ideal required preload for this scenario: F_req = F_shear / (N * base_mu) + 1.5
    f_shear_max = max(1.5, abs(scenario["v_shear"] * 1000.0 * 0.5))
    required_fn = float(np.clip((f_shear_max / (n_contacts * scenario["base_mu"])) + 1.5, FN_MIN, FN_MAX))

    return {
        "seed": seed,
        "split": scenario["split"],
        "controller": controller_name,
        "protocol": protocol,
        "num_contacts": n_contacts,
        "base_mu": scenario["base_mu"],
        "disturbance": scenario["disturbance"],
        "initial_preload_nominal": init_fn,
        "selected_preload": selected_preload,
        "required_preload_ground_truth": required_fn,
        "preload_error": selected_preload - required_fn,
        "is_under_preloaded": bool(selected_preload < required_fn - 1.0),
        "is_over_preloaded": bool(selected_preload > required_fn + 6.0),
        "grasp_success": bool(info["grasp_success"]),
        "is_gross_slipping": bool(info["is_gross_slipping"]),
        "gross_slip_step": int(info["gross_slip_step"]),
        "mean_total_fn": float(info["impulse_total"] / max(1e-4, env.time)),
        "peak_fn_max": float(info["peak_fn_max"]),
        "impulse_total_Ns": float(info["impulse_total"]),
        "mechanical_work_act_J": float(info["mechanical_work_act"]),
        "tangential_work_J": float(info["tangential_work"]),
        "damage_proxy": float(info["damage_proxy"]),
        "wrench_residual_norm": float(info["wrench_residual_norm"]),
        "slew_violations": slew_violations,
        "bound_violations": bound_violations,
        "budget_violations": budget_violations,
        "latency_mean_ms": float(np.mean(latencies)),
        "latency_p95_ms": float(np.percentile(latencies, 95)),
        "latency_max_ms": float(np.max(latencies)),
        "estimation_info": estimation_info,
    }


def run_batch_tasks(tasks: List[Dict[str, Any]], workers: int = 8) -> List[Dict[str, Any]]:
    """Runs tasks in parallel using ProcessPoolExecutor."""
    if not tasks:
        return []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(run_single_episode, tasks, chunksize=4))


def summarize_results(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes comprehensive metrics over an experimental run."""
    n = len(records)
    if n == 0:
        return {}

    successes = [1.0 if r["grasp_success"] else 0.0 for r in records]
    success_rate = float(np.mean(successes))

    # Stratified by friction
    by_mu = {}
    for mu in FRICTION_LEVELS:
        sub = [r for r in records if abs(r["base_mu"] - mu) < 1e-4]
        by_mu[str(mu)] = {
            "success_rate": float(np.mean([1.0 if r["grasp_success"] else 0.0 for r in sub])) if sub else 0.0,
            "count": len(sub),
        }

    # Stratified by topology
    by_n = {}
    for n_c in TOPOLOGIES:
        sub = [r for r in records if r["num_contacts"] == n_c]
        by_n[str(n_c)] = {
            "success_rate": float(np.mean([1.0 if r["grasp_success"] else 0.0 for r in sub])) if sub else 0.0,
            "count": len(sub),
        }

    return {
        "episodes": n,
        "grasp_success_rate": success_rate,
        "success_by_friction": by_mu,
        "success_by_topology": by_n,
        "mean_total_fn": float(np.mean([r["mean_total_fn"] for r in records])),
        "peak_fn_max": float(np.max([r["peak_fn_max"] for r in records])),
        "mean_impulse_Ns": float(np.mean([r["impulse_total_Ns"] for r in records])),
        "mean_mech_work_act_J": float(np.mean([r["mechanical_work_act_J"] for r in records])),
        "mean_tangential_work_J": float(np.mean([r["tangential_work_J"] for r in records])),
        "mean_damage_proxy": float(np.mean([r["damage_proxy"] for r in records])),
        "slew_violations": int(sum(r["slew_violations"] for r in records)),
        "bound_violations": int(sum(r["bound_violations"] for r in records)),
        "budget_violations": int(sum(r["budget_violations"] for r in records)),
        "max_wrench_residual": float(np.max([r["wrench_residual_norm"] for r in records])),
        "latency_mean_ms": float(np.mean([r["latency_mean_ms"] for r in records])),
        "latency_p95_ms": float(np.percentile([r["latency_p95_ms"] for r in records], 95)),
        "latency_worst_ms": float(np.max([r["latency_max_ms"] for r in records])),
        "under_preloading_rate": float(np.mean([1.0 if r["is_under_preloaded"] else 0.0 for r in records])),
        "over_preloading_rate": float(np.mean([1.0 if r["is_over_preloaded"] else 0.0 for r in records])),
        "mean_preload_error": float(np.mean([r["preload_error"] for r in records])),
    }


def mcnemar_paired(proposed: List[Dict[str, Any]], baseline: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Computes exact two-sided McNemar test and bootstrap 95% CI."""
    base_map = {r["seed"]: r for r in baseline}
    b_only = 0
    c_only = 0
    diffs = []

    for r in proposed:
        seed = r["seed"]
        if seed not in base_map:
            continue
        ya = 1.0 if r["grasp_success"] else 0.0
        yb = 1.0 if base_map[seed]["grasp_success"] else 0.0
        diffs.append(ya - yb)
        if ya == 1.0 and yb == 0.0:
            b_only += 1
        elif ya == 0.0 and yb == 1.0:
            c_only += 1

    n_disc = b_only + c_only
    p_val = 1.0 if n_disc == 0 else float(binomtest(b_only, n_disc, 0.5, alternative="two-sided").pvalue)

    # Bootstrap 95% CI
    diff_arr = np.asarray(diffs, dtype=float) if diffs else np.zeros(1)
    rng = np.random.RandomState(20261124)
    boots = [float(np.mean(diff_arr[rng.randint(0, len(diff_arr), len(diff_arr))])) for _ in range(2000)]
    ci_lo, ci_hi = np.percentile(boots, [2.5, 97.5])

    return {
        "n_pairs": len(diffs),
        "b_proposed_only": b_only,
        "c_baseline_only": c_only,
        "difference_pp": float(np.mean(diffs) * 100.0) if diffs else 0.0,
        "difference_95ci_pp": [float(ci_lo * 100.0), float(ci_hi * 100.0)],
        "exact_p_two_sided": p_val,
    }
