"""
HOROS PROBE-1 — Phase 5 Task 4R4
Main Experiment Orchestration & Benchmark Suite
File: run_p5_t4r4_experiments.py
"""

from __future__ import annotations

import _paths  # noqa: F401
import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Dict, Any, List
import numpy as np

from preload_experiment_protocol import (
    TRAIN_RANGE,
    VAL_RANGE,
    TEST_RANGE,
    STRESS_RANGE,
    SWEEP_TARGETS,
    FRICTION_LEVELS,
    TOPOLOGIES,
    PROTOCOL_A,
    PROTOCOL_B,
)
from preload_controller_baselines import get_initial_preload
from preload_sweep_experiment import (
    generate_scenario,
    run_batch_tasks,
    summarize_results,
    mcnemar_paired,
)

HERE = Path(__file__).resolve().parent

ALL_CONTROLLERS = [
    "Fixed_3N",
    "Fixed_5N",
    "Fixed_8N",
    "Fixed_12N",
    "Fixed_16N",
    "Fixed_20N",
    "preload_25",
    "P5T3Cooperative",
    "P5T4R2Topology14",
    "AdaptivePreloadPlanner",
]


def execute_controlled_preload_sweep(train_scenarios: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sweeps fixed normal-force targets: 3, 5, 8, 12, 16, 20, 25 N."""
    print("\n--- STAGE 1: CONTROLLED INITIAL PRELOAD SWEEP (TRAINING SPLIT) ---")
    sweep_tasks = []
    for scen in train_scenarios:
        for fn_target in SWEEP_TARGETS:
            sweep_tasks.append({
                "scenario": scen,
                "controller": f"Fixed_{int(fn_target)}N" if fn_target != 25.0 else "preload_25",
                "protocol": PROTOCOL_A,
                "initial_preload": fn_target,
            })

    print(f"Executing {len(sweep_tasks)} sweep episodes across 7 preload targets...")
    sweep_records = run_batch_tasks(sweep_tasks, workers=8)

    sweep_summary = {}
    for fn_target in SWEEP_TARGETS:
        ctrl_name = f"Fixed_{int(fn_target)}N" if fn_target != 25.0 else "preload_25"
        sub = [r for r in sweep_records if r["initial_preload_nominal"] == fn_target]
        sweep_summary[f"{fn_target:.1f}N"] = summarize_results(sub)
        print(f"  Preload {fn_target:4.1f} N: Success = {sweep_summary[f'{fn_target:.1f}N']['grasp_success_rate']:.4f}, "
              f"Impulse = {sweep_summary[f'{fn_target:.1f}N']['mean_impulse_Ns']:.2f} Ns, "
              f"Work = {sweep_summary[f'{fn_target:.1f}N']['mean_mech_work_act_J']*1000.0:.2f} mJ, "
              f"Damage = {sweep_summary[f'{fn_target:.1f}N']['mean_damage_proxy']:.2f}")

    return {
        "sweep_summary": sweep_summary,
        "raw_records": sweep_records,
    }


def execute_friction_estimation_validation(val_scenarios: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Evaluates friction estimator and uncertainty model on validation partition."""
    print("\n--- STAGE 2: PRE-CONTACT FRICTION ESTIMATION & UNCERTAINTY AUDIT ---")
    val_tasks = []
    for scen in val_scenarios:
        val_tasks.append({
            "scenario": scen,
            "controller": "AdaptivePreloadPlanner",
            "protocol": PROTOCOL_A,
            "initial_preload": 4.0,
        })

    val_records = run_batch_tasks(val_tasks, workers=8)

    errors = []
    abs_errors = []
    sigmas = []
    confidences = []
    fallback_count = 0
    covered_95 = 0

    for r in val_records:
        true_mu = r["base_mu"]
        info = r["estimation_info"]
        if "mu_hats" in info and info["mu_hats"]:
            est_mu = float(np.mean(info["mu_hats"]))
            est_sig = float(np.mean(info["mu_sigmas"]))
            conf = float(np.mean(info["confidences"]))
            err = est_mu - true_mu
            errors.append(err)
            abs_errors.append(abs(err))
            sigmas.append(est_sig)
            confidences.append(conf)

            # Check 95% coverage: true_mu >= mu_safe
            if true_mu >= info["min_mu_safe"] - 1e-4:
                covered_95 += 1

            if info.get("strategy") == "fallback_conservative":
                fallback_count += 1

    n_val = max(1, len(errors))
    estimation_summary = {
        "val_episodes": len(val_records),
        "mean_estimation_error": float(np.mean(errors)) if errors else 0.0,
        "mean_abs_error": float(np.mean(abs_errors)) if abs_errors else 0.0,
        "rmse": float(math.sqrt(np.mean(np.array(errors)**2))) if errors else 0.0,
        "mean_reported_sigma": float(np.mean(sigmas)) if sigmas else 0.0,
        "mean_confidence": float(np.mean(confidences)) if confidences else 0.0,
        "coverage_95_rate": float(covered_95 / n_val),
        "fallback_trigger_rate": float(fallback_count / n_val),
    }

    print(f"  Validation Estimation RMSE: {estimation_summary['rmse']:.4f}")
    print(f"  Mean Absolute Error (MAE): {estimation_summary['mean_abs_error']:.4f}")
    print(f"  95% Conservative Coverage: {estimation_summary['coverage_95_rate']*100.0:.2f}%")
    print(f"  Fallback Trigger Rate: {estimation_summary['fallback_trigger_rate']*100.0:.2f}%")

    return {
        "summary": estimation_summary,
        "raw_records": val_records,
    }


def execute_heldout_benchmark(test_scenarios: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Runs all 10 controllers across the 600 held-out evaluation scenarios under Protocol A and B."""
    print("\n--- STAGE 3: 10-CONTROLLER HELDOUT EVALUATION (600 EPISODES) ---")
    tasks_a = []
    tasks_b = []

    for scen in test_scenarios:
        for ctrl_name in ALL_CONTROLLERS:
            tasks_a.append({
                "scenario": scen,
                "controller": ctrl_name,
                "protocol": PROTOCOL_A,
                "initial_preload": get_initial_preload(ctrl_name, PROTOCOL_A),
            })
            tasks_b.append({
                "scenario": scen,
                "controller": ctrl_name,
                "protocol": PROTOCOL_B,
                "initial_preload": get_initial_preload(ctrl_name, PROTOCOL_B),
            })

    print(f"Executing Protocol A ({len(tasks_a)} episodes across 10 controllers)...")
    records_a = run_batch_tasks(tasks_a, workers=8)
    print(f"Executing Protocol B ({len(tasks_b)} episodes across 10 controllers)...")
    records_b = run_batch_tasks(tasks_b, workers=8)

    summary_a = {}
    summary_b = {}
    for ctrl_name in ALL_CONTROLLERS:
        sub_a = [r for r in records_a if r["controller"] == ctrl_name]
        sub_b = [r for r in records_b if r["controller"] == ctrl_name]
        summary_a[ctrl_name] = summarize_results(sub_a)
        summary_b[ctrl_name] = summarize_results(sub_b)

    # Paired McNemar comparisons for Protocol A against AdaptivePreloadPlanner
    proposed_a = [r for r in records_a if r["controller"] == "AdaptivePreloadPlanner"]
    paired_a = {}
    for ctrl_name in ALL_CONTROLLERS:
        if ctrl_name == "AdaptivePreloadPlanner":
            continue
        base_a = [r for r in records_a if r["controller"] == ctrl_name]
        paired_a[ctrl_name] = mcnemar_paired(proposed_a, base_a)

    return {
        "summary_protocol_a": summary_a,
        "summary_protocol_b": summary_b,
        "paired_protocol_a": paired_a,
        "raw_records_protocol_a": records_a,
        "raw_records_protocol_b": records_b,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Run quick pilot for verification")
    args = parser.parse_args()

    n_train = 30 if args.quick else (TRAIN_RANGE[1] - TRAIN_RANGE[0])
    n_val = 30 if args.quick else (VAL_RANGE[1] - VAL_RANGE[0])
    n_test = 60 if args.quick else (TEST_RANGE[1] - TEST_RANGE[0])

    print(f"Generating partitions: Train={n_train}, Val={n_val}, Test={n_test}...")
    train_scenarios = [generate_scenario(TRAIN_RANGE[0] + i, "train") for i in range(n_train)]
    val_scenarios = [generate_scenario(VAL_RANGE[0] + i, "val") for i in range(n_val)]
    test_scenarios = [generate_scenario(TEST_RANGE[0] + i, "test") for i in range(n_test)]

    # 1. Sweep
    sweep_results = execute_controlled_preload_sweep(train_scenarios)
    (HERE / "preload_sweep_results.json").write_text(json.dumps(sweep_results["sweep_summary"], indent=2), encoding="utf-8")

    # 2. Estimation Validation
    est_results = execute_friction_estimation_validation(val_scenarios)
    (HERE / "preload_estimation_results.json").write_text(json.dumps(est_results["summary"], indent=2), encoding="utf-8")

    # 3. Held-out Benchmark
    bench_results = execute_heldout_benchmark(test_scenarios)

    # 4. Save JSON deliverables
    (HERE / "preload_raw_records.json").write_text(json.dumps(
        bench_results["raw_records_protocol_a"] + bench_results["raw_records_protocol_b"],
    ), encoding="utf-8")

    eval_summary = {
        "benchmark": "P5-T4R4 Adaptive Preload Policy Validation",
        "held_out_scenarios": len(test_scenarios),
        "protocol_a_summary": bench_results["summary_protocol_a"],
        "protocol_b_summary": bench_results["summary_protocol_b"],
        "paired_contrasts_protocol_a": bench_results["paired_protocol_a"],
        "friction_estimation_validation": est_results["summary"],
        "preload_sweep_training": sweep_results["sweep_summary"],
    }
    (HERE / "preload_statistical_analysis.json").write_text(json.dumps(bench_results["paired_protocol_a"], indent=2), encoding="utf-8")

    # 5. Failure Analysis
    failures = {}
    for ctrl in ALL_CONTROLLERS:
        sub = [r for r in bench_results["raw_records_protocol_a"] if r["controller"] == ctrl]
        failed_eps = [r for r in sub if not r["grasp_success"]]
        by_dist = {}
        for r in failed_eps:
            d = r["disturbance"]
            by_dist[d] = by_dist.get(d, 0) + 1
        failures[ctrl] = {
            "total_failures": len(failed_eps),
            "failure_rate": len(failed_eps) / max(1, len(sub)),
            "failures_by_disturbance": by_dist,
        }
    (HERE / "preload_failure_analysis.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")

    # 6. Repro Manifest
    repro = {
        "milestone": "P5-T4R4",
        "train_seeds": list(TRAIN_RANGE),
        "val_seeds": list(VAL_RANGE),
        "test_seeds": list(TEST_RANGE),
        "controllers": ALL_CONTROLLERS,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (HERE / "preload_reproducibility_manifest.json").write_text(json.dumps(repro, indent=2), encoding="utf-8")

    print("\nBenchmark complete! All JSON artifacts saved.")


if __name__ == "__main__":
    main()
