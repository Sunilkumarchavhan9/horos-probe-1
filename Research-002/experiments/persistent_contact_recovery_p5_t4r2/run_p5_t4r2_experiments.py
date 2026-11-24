"""Train selection, validation baseline choice, then held-out scoring."""

from __future__ import annotations

import _paths  # noqa: F401
import argparse
import json
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from scipy.stats import binomtest

from feasibility_aware_recovery_controller import FeasibilityAwareRecoveryController
from p5_t4_controller_baselines import P5T3RecoveryNoFeasibility
from p5_t4_sim_env import FeasibilityGraspEnv
from p5_t4r2_experiment_protocol import (
    PROTOCOL_A,
    PROTOCOL_B,
    SELECTION_MODES,
    STRESS_QUOTA,
    STRESS_RANGE,
    TEST_QUOTA,
    TEST_RANGE,
    TRAIN_QUOTA,
    TRAIN_RANGE,
    VAL_QUOTA,
    VAL_RANGE,
)
from p5_t4r2_scenario_generator import generate_partition
from p5_t4r2_sim_env import LifecycleGraspEnv
from predictive_recovery_controller import CorrectiveRecoveryController
from topology_aware_recovery_controller import TopologyAwareRecoveryController

HERE = Path(__file__).resolve().parent
MAIN_CONTROLLERS = (
    "P5T3RecoveryNoFeasibility",
    "P5T4FeasibilityAwareRecovery",
    "preload_25",
    "P5T4R2TopologyAware",
)


def make_env(scenario, protocol, plant: str):
    if plant == "archive":
        return FeasibilityGraspEnv(scenario, protocol)
    if plant == "oneshot_original":
        return LifecycleGraspEnv(scenario, protocol, semantics="oneshot", finger="original")
    return LifecycleGraspEnv(scenario, protocol, semantics="oneshot", finger="physical")


def make_controller(name: str, num_contacts: int, config: Dict[str, Any], seed: int):
    if name == "P5T3RecoveryNoFeasibility":
        return P5T3RecoveryNoFeasibility(num_contacts)
    if name == "P5T4FeasibilityAwareRecovery":
        return FeasibilityAwareRecoveryController(num_contacts, residual_mode="oppose_load", p_low=0.20, p_high=0.65, seed=seed)
    if name == "preload_25":
        return CorrectiveRecoveryController(num_contacts, mode="preload_25", seed=seed)
    level = float(str(config.get("mode", "topology_10")).split("_")[1])
    return TopologyAwareRecoveryController(num_contacts, per_contact=level, seed=seed)


def _episode(task: Dict[str, Any]) -> Dict[str, Any]:
    scenario = task["scenario"]
    protocol = task["protocol"]
    env = make_env(scenario, protocol, task["plant"])
    obs = env.reset()
    ctrl = make_controller(task["controller"], int(scenario["num_contacts"]), task["config"], int(scenario["seed"]))
    ctrl.reset(obs)
    latencies = []
    decision = None
    probability = None
    while True:
        t0 = time.perf_counter()
        command = ctrl.step(obs)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        if decision is None and command.get("decision_label") is not None:
            decision = command["decision_label"]
            probability = command.get("feasibility_probability")
        obs, info = env.step(command)
        if info["done"]:
            break
    oracle = bool(scenario["oracle_protocol_a"] if protocol == PROTOCOL_A else scenario["oracle_protocol_b"])
    impact = info["impact_speed"]
    no_escalation = float(info["max_post_degrade_increase"]) <= 0.25
    bounds_ok = int(info["applied_bound_violations"]) == 0 and int(info["applied_slew_violations"]) == 0
    if bool(info["supported"]) and impact is not None and float(impact) <= 0.35 and no_escalation and bounds_ok:
        terminal = "SUPPORT_REST"
    elif decision in ("INFEASIBLE", "UNCERTAIN") and no_escalation and bounds_ok:
        terminal = "CONTROLLED_RELEASE"
    elif bool(info["grasp_held"]) and decision in ("FEASIBLE", "DEGRADED_RECOVERY", None):
        terminal = "GRASP_HELD"
    else:
        terminal = "FAILED"
    return {
        "seed": int(scenario["seed"]),
        "split": scenario["split"],
        "protocol": protocol,
        "controller": task["controller"],
        "plant": task["plant"],
        "ablation": task["ablation"],
        "num_contacts": int(scenario["num_contacts"]),
        "event": scenario["event"],
        "oracle_feasible": oracle,
        "recontact_oracle_possible": bool(scenario.get("recontact_oracle_possible", True)),
        "grasp_recovery": bool(info["grasp_held"]),
        "decision_label": decision,
        "feasibility_probability": probability,
        "terminal_state": terminal,
        "safe_termination": terminal in ("SUPPORT_REST", "CONTROLLED_RELEASE"),
        "false_feasible": (not oracle) and decision == "FEASIBLE",
        "false_infeasible": bool(oracle and decision == "INFEASIBLE"),
        "predicted_infeasible": decision == "INFEASIBLE",
        "displacement_mm": float(info["displacement_m"] * 1000.0),
        "peak_speed": float(info["peak_speed"]),
        "peak_omega": float(info.get("peak_omega", 0.0)),
        "peak_fn": float(info["peak_fn"]),
        "mean_total_fn": float(info["impulse"] / max(float(info["time"]), 1e-6)),
        "impulse": float(info["impulse"]),
        "work_normal_J": float(info["work_normal"]),
        "work_tangent_J": float(info["work_tangent"]),
        "damage_proxy": float(info["damage"]),
        "applied_budget_violations": int(info["applied_budget_violations"]),
        "applied_bound_violations": int(info["applied_bound_violations"]),
        "applied_slew_violations": int(info["applied_slew_violations"]),
        "consistency_violations": int(info["consistency_violations"]),
        "invalid_force_violations": int(info["invalid_force_violations"]),
        "recontact_successes": int(info["recontact_successes"]),
        "recontact_attempts": int(info["recontact_attempts"]),
        "formations": int(info.get("formations", 0)),
        "verified_contacts": int(info.get("verified_contacts", 0)),
        "max_persist": int(info.get("max_persist", 0)),
        "loss_calls": int(info.get("loss_calls", 0)),
        "loss_applications": int(info.get("loss_applications", 0)),
        "events_consumed": int(info.get("events_consumed", 0)),
        "event_count": int(info.get("event_count", 0)),
        "latency_mean_ms": float(np.mean(latencies)),
        "latency_p95_ms": float(np.percentile(latencies, 95)),
        "latency_max_ms": float(np.max(latencies)),
    }


def run_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not tasks:
        return []
    with ProcessPoolExecutor(max_workers=8) as pool:
        return list(pool.map(_episode, tasks, chunksize=4))


def _tasks(scenarios, controllers, protocols, config, plant: str, ablation: str):
    tasks = []
    for scenario in scenarios:
        for protocol in protocols:
            for name in controllers:
                tasks.append({
                    "scenario": scenario,
                    "protocol": protocol,
                    "controller": name,
                    "config": config,
                    "plant": plant,
                    "ablation": ablation,
                })
    return tasks


def feasible_rate(records: List[Dict[str, Any]]) -> float:
    rows = [r for r in records if r["oracle_feasible"]]
    if not rows:
        return float("nan")
    return float(np.mean([1.0 if r["grasp_recovery"] else 0.0 for r in rows]))


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    feasible = [r for r in records if r["oracle_feasible"]]
    infeasible = [r for r in records if not r["oracle_feasible"]]
    labeled_infeasible = [r for r in infeasible if r["decision_label"] is not None]
    by_n = {}
    for n in (3, 4, 5):
        subset = [r for r in feasible if r["num_contacts"] == n]
        by_n[str(n)] = {
            "feasible_recovery": float(np.mean([1.0 if r["grasp_recovery"] else 0.0 for r in subset])) if subset else None,
            "n_feasible": len(subset),
        }
    recontact_rows = [r for r in records if r["recontact_oracle_possible"] and r["event"] != "friction_collapse" and r["event"] != "gradual"]
    return {
        "episodes": len(records),
        "feasible_recovery": feasible_rate(records),
        "n_feasible": len(feasible),
        "n_infeasible": len(infeasible),
        "overall_grasp_recovery": float(np.mean([1.0 if r["grasp_recovery"] else 0.0 for r in records])) if records else None,
        "safe_termination_on_infeasible": float(np.mean([1.0 if r["safe_termination"] else 0.0 for r in infeasible])) if infeasible else None,
        "infeasible_recall": float(np.mean([1.0 if r["predicted_infeasible"] else 0.0 for r in labeled_infeasible])) if labeled_infeasible else None,
        "false_feasible_rate": float(np.mean([1.0 if r["false_feasible"] else 0.0 for r in infeasible])) if infeasible else None,
        "false_infeasible_rate": float(np.mean([1.0 if r["false_infeasible"] else 0.0 for r in feasible])) if feasible else None,
        "by_topology_feasible": by_n,
        "mean_total_fn": float(np.mean([r["mean_total_fn"] for r in records])) if records else None,
        "peak_fn_max": float(np.max([r["peak_fn"] for r in records])) if records else None,
        "mean_impulse": float(np.mean([r["impulse"] for r in records])) if records else None,
        "mean_work_normal_J": float(np.mean([r["work_normal_J"] for r in records])) if records else None,
        "mean_work_tangent_J": float(np.mean([r["work_tangent_J"] for r in records])) if records else None,
        "mean_damage": float(np.mean([r["damage_proxy"] for r in records])) if records else None,
        "formations": int(sum(r["formations"] for r in records)),
        "verified_contacts": int(sum(r["verified_contacts"] for r in records)),
        "recontact_attempts": int(sum(r["recontact_attempts"] for r in records)),
        "recontact_successes": int(sum(r["recontact_successes"] for r in records)),
        "mean_max_persist": float(np.mean([r["max_persist"] for r in recontact_rows])) if recontact_rows else None,
        "applied_budget_violations": int(sum(r["applied_budget_violations"] for r in records)),
        "applied_bound_violations": int(sum(r["applied_bound_violations"] for r in records)),
        "applied_slew_violations": int(sum(r["applied_slew_violations"] for r in records)),
        "consistency_violations": int(sum(r["consistency_violations"] for r in records)),
        "invalid_force_violations": int(sum(r["invalid_force_violations"] for r in records)),
        "latency_mean_ms": float(np.mean([r["latency_mean_ms"] for r in records])) if records else None,
        "latency_p95_ms": float(np.percentile([r["latency_p95_ms"] for r in records], 95)) if records else None,
        "latency_worst_ms": float(np.max([r["latency_max_ms"] for r in records])) if records else None,
    }


def mcnemar(proposed: List[Dict[str, Any]], baseline: List[Dict[str, Any]]) -> Dict[str, Any]:
    other = {(r["seed"], r["protocol"]): r for r in baseline}
    b_only = c_only = 0
    diffs = []
    for row in proposed:
        if not row["oracle_feasible"]:
            continue
        match = other[(row["seed"], row["protocol"])]
        if not match["oracle_feasible"]:
            continue
        ya = 1.0 if row["grasp_recovery"] else 0.0
        yb = 1.0 if match["grasp_recovery"] else 0.0
        diffs.append(ya - yb)
        b_only += int(ya == 1.0 and yb == 0.0)
        c_only += int(ya == 0.0 and yb == 1.0)
    n_disc = b_only + c_only
    p_value = 1.0 if n_disc == 0 else float(binomtest(b_only, n_disc, 0.5, alternative="two-sided").pvalue)
    arr = np.asarray(diffs, dtype=float) if diffs else np.zeros(1)
    rng = np.random.RandomState(20261124)
    boots = [float(np.mean(arr[rng.randint(0, len(arr), len(arr))])) for _ in range(2000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "n_feasible": len(diffs),
        "b_proposed_only": b_only,
        "c_baseline_only": c_only,
        "difference": float(np.mean(diffs)) if diffs else None,
        "difference_95ci": [float(lo), float(hi)],
        "exact_p_two_sided": p_value,
    }


def write_report(path: Path, payload: Dict[str, Any]) -> None:
    proposed = payload["summary"]["P5T4R2TopologyAware"][PROTOCOL_A]
    preload = payload["summary"]["preload_25"][PROTOCOL_A]
    baseline_name = payload["frozen"]["primary_baseline"]
    paired = payload["paired_protocol_a"]
    lines = [
        "# P5-T4R2 scientific evaluation",
        "",
        f"Verdict: **{payload['verdict']}**.",
        "",
        "Archived verdicts are unchanged: P5-T3 CONDITIONAL SCIENTIFIC PASS, P5-T4 FAIL, P5-T4 corrective continuation FAIL. Hardware was not used.",
        "",
        f"Primary baseline chosen on validation: `{baseline_name}`. Frozen topology mode: `{payload['frozen']['selected_mode']}`.",
        "",
        f"Protocol A topology-aware feasible recovery {proposed['feasible_recovery']:.4f} on {proposed['n_feasible']} feasible episodes. Infeasible recall {proposed['infeasible_recall']}. False-feasible rate {proposed['false_feasible_rate']}. Safe termination {proposed['safe_termination_on_infeasible']}. Mean force {proposed['mean_total_fn']:.3f} N. P95 latency {proposed['latency_p95_ms']:.4f} ms.",
        "",
        f"By contact count: N=3 {proposed['by_topology_feasible']['3']['feasible_recovery']}, N=4 {proposed['by_topology_feasible']['4']['feasible_recovery']}, N=5 {proposed['by_topology_feasible']['5']['feasible_recovery']}.",
        "",
        f"Paired difference versus {baseline_name}: {paired[baseline_name]['difference']} (p = {paired[baseline_name]['exact_p_two_sided']}). Versus preload_25: {paired['preload_25']['difference']} (p = {paired['preload_25']['exact_p_two_sided']}). preload_25 feasible recovery on this plant is {preload['feasible_recovery']:.4f}.",
        "",
        f"Ablation A (repeat scheduler, preload_25) feasible recovery {payload['ablation']['A_repeat_preload']['feasible_recovery']:.4f}. Ablation B (one-shot, original finger, preload_25) {payload['ablation']['B_oneshot_original_finger']['feasible_recovery']:.4f}. Ablation D (one-shot, physical finger, preload_25) {payload['ablation']['D_physical_preload']['feasible_recovery']:.4f}.",
        "",
        "The repeated-loss defect is real: the archived scheduler calls the loss again whenever the contact is no longer in the lost set, and the previous finger model then cleared the normal force. One-shot consumption is reported above as its own ablation. A higher grasp-recovery number from that repair is not, by itself, a gain in cooperative control.",
        "",
        "N=3 remains the weak topology when the object leaves the finger's reach before a loaded contact can persist. Further simulation work is required before any hardware claim. Hardware remains prohibited.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def score_verdict(proposed: Dict[str, Any], paired_primary: Dict[str, Any], paired_preload: Dict[str, Any], replay_ok: bool) -> str:
    by_n = proposed["by_topology_feasible"]
    each = min(by_n["3"]["feasible_recovery"], by_n["4"]["feasible_recovery"], by_n["5"]["feasible_recovery"])
    safety = (
        proposed["false_feasible_rate"] <= 0.05
        and proposed["applied_budget_violations"] == 0
        and proposed["applied_bound_violations"] == 0
        and proposed["applied_slew_violations"] == 0
        and proposed["consistency_violations"] == 0
        and proposed["invalid_force_violations"] == 0
        and replay_ok
    )
    full = safety and proposed["infeasible_recall"] >= 0.95 and proposed["feasible_recovery"] >= 0.80 and each >= 0.70 and proposed["safe_termination_on_infeasible"] >= 0.90 and proposed["latency_p95_ms"] <= 1.0
    beats = paired_primary["difference"] > 0 and paired_primary["exact_p_two_sided"] < 0.025 and paired_preload["difference"] > 0 and paired_preload["exact_p_two_sided"] < 0.025
    if full and beats:
        return "PASS"
    conditional = safety and proposed["infeasible_recall"] >= 0.85 and proposed["feasible_recovery"] >= 0.65 and each >= 0.55 and proposed["safe_termination_on_infeasible"] >= 0.75 and proposed["latency_p95_ms"] <= 2.0 and paired_primary["difference"] > 0
    if conditional:
        return "CONDITIONAL PASS"
    return "FAIL"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--select-only", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    args = parser.parse_args()
    frozen_path = HERE / "p5_t4r2_frozen_configuration.json"
    bundle_path = HERE / "p5_t4r2_scenarios.json"
    if args.select_only or not frozen_path.exists():
        bundle = {
            "train": generate_partition("train", TRAIN_RANGE[0], TRAIN_RANGE[1], TRAIN_QUOTA),
            "validation": generate_partition("validation", VAL_RANGE[0], VAL_RANGE[1], VAL_QUOTA),
            "test": generate_partition("test", TEST_RANGE[0], TEST_RANGE[1], TEST_QUOTA),
            "stress": generate_partition("stress", STRESS_RANGE[0], STRESS_RANGE[1], STRESS_QUOTA),
        }
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        (HERE / "p5_t4r2_heldout_manifest.json").write_text(json.dumps({
            "count": len(bundle["test"]["scenarios"]),
            "shortfall": bundle["test"]["shortfall"],
            "seeds": [int(s["seed"]) for s in bundle["test"]["scenarios"]],
            "event_ids": [
                {"seed": int(s["seed"]), "event": s["event"], "lost_ids": s["lost_ids"], "extra_events": s.get("extra_events", [])}
                for s in bundle["test"]["scenarios"]
            ],
        }), encoding="utf-8")
        selection = []
        for mode in SELECTION_MODES:
            rows = run_tasks(_tasks(bundle["train"]["scenarios"], ["P5T4R2TopologyAware"], [PROTOCOL_A], {"mode": mode}, "oneshot_physical", "selection"))
            for row in rows:
                row["controller"] = mode
            selection.extend(rows)
        ranked = []
        for mode in SELECTION_MODES:
            subset = [r for r in selection if r["controller"] == mode]
            ranked.append({"mode": mode, "feasible_recovery": feasible_rate(subset), "mean_total_fn": float(np.mean([r["mean_total_fn"] for r in subset]))})
        ranked.sort(key=lambda item: (-(item["feasible_recovery"] or -1), item["mean_total_fn"]))
        val_rows = run_tasks(_tasks(
            bundle["validation"]["scenarios"],
            ["P5T3RecoveryNoFeasibility", "P5T4FeasibilityAwareRecovery", "preload_25"],
            [PROTOCOL_A], {"mode": "preload_25"}, "oneshot_physical", "validation_baseline",
        ))
        baselines = []
        for name in ("P5T3RecoveryNoFeasibility", "P5T4FeasibilityAwareRecovery", "preload_25"):
            subset = [r for r in val_rows if r["controller"] == name]
            baselines.append({"name": name, "feasible_recovery": feasible_rate(subset)})
        baselines.sort(key=lambda item: -(item["feasible_recovery"] or -1))
        frozen = {
            "selected_mode": ranked[0]["mode"],
            "selection": ranked,
            "primary_baseline": baselines[0]["name"],
            "validation_baselines": baselines,
            "held_out_count": len(bundle["test"]["scenarios"]),
        }
        frozen_path.write_text(json.dumps(frozen, indent=2), encoding="utf-8")
        print("FROZEN", json.dumps(frozen))
        if args.select_only:
            return
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    config = {"mode": frozen["selected_mode"]}
    test = bundle["test"]["scenarios"]
    main_rows = run_tasks(_tasks(test, MAIN_CONTROLLERS, [PROTOCOL_A, PROTOCOL_B], config, "oneshot_physical", "E_full"))
    ablation_a = run_tasks(_tasks(test, ["preload_25"], [PROTOCOL_A], config, "archive", "A_repeat_preload"))
    ablation_b = run_tasks(_tasks(test, ["preload_25"], [PROTOCOL_A], config, "oneshot_original", "B_oneshot_original_finger"))
    stress = run_tasks(_tasks(bundle["stress"]["scenarios"], ["P5T4R2TopologyAware"], [PROTOCOL_A], config, "oneshot_physical", "stress"))
    replay = run_tasks(_tasks(test[:20], ["P5T4R2TopologyAware"], [PROTOCOL_A], config, "oneshot_physical", "replay"))
    original = {(r["seed"]): r["grasp_recovery"] for r in main_rows if r["controller"] == "P5T4R2TopologyAware" and r["protocol"] == PROTOCOL_A}
    replay_ok = all(original[r["seed"]] == r["grasp_recovery"] for r in replay)
    summary = {}
    for name in MAIN_CONTROLLERS:
        summary[name] = {
            PROTOCOL_A: summarize([r for r in main_rows if r["controller"] == name and r["protocol"] == PROTOCOL_A]),
            PROTOCOL_B: summarize([r for r in main_rows if r["controller"] == name and r["protocol"] == PROTOCOL_B]),
        }
    ablation = {
        "A_repeat_preload": summarize(ablation_a),
        "B_oneshot_original_finger": summarize(ablation_b),
        "D_physical_preload": summary["preload_25"][PROTOCOL_A],
        "E_topology": summary["P5T4R2TopologyAware"][PROTOCOL_A],
        "stress": summarize(stress),
    }
    proposed_a = [r for r in main_rows if r["controller"] == "P5T4R2TopologyAware" and r["protocol"] == PROTOCOL_A]
    paired = {}
    for name in MAIN_CONTROLLERS:
        if name == "P5T4R2TopologyAware":
            continue
        paired[name] = mcnemar(proposed_a, [r for r in main_rows if r["controller"] == name and r["protocol"] == PROTOCOL_A])
    verdict = score_verdict(summary["P5T4R2TopologyAware"][PROTOCOL_A], paired[frozen["primary_baseline"]], paired["preload_25"], replay_ok)
    payload = {"verdict": verdict, "frozen": frozen, "summary": summary, "ablation": ablation, "paired_protocol_a": paired, "replay_match": replay_ok}
    (HERE / "p5_t4r2_evaluation_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (HERE / "p5_t4r2_raw_records.json").write_text(json.dumps(main_rows + ablation_a + ablation_b + stress), encoding="utf-8")
    (HERE / "p5_t4r2_statistical_analysis.json").write_text(json.dumps({"paired_protocol_a": paired, "primary_baseline": frozen["primary_baseline"]}, indent=2), encoding="utf-8")
    (HERE / "p5_t4r2_recontact_analysis.json").write_text(json.dumps({
        "protocol_a_topology": {
            "formations": summary["P5T4R2TopologyAware"][PROTOCOL_A]["formations"],
            "verified_contacts": summary["P5T4R2TopologyAware"][PROTOCOL_A]["verified_contacts"],
            "attempts": summary["P5T4R2TopologyAware"][PROTOCOL_A]["recontact_attempts"],
            "success_counter": summary["P5T4R2TopologyAware"][PROTOCOL_A]["recontact_successes"],
            "mean_max_persist_steps": summary["P5T4R2TopologyAware"][PROTOCOL_A]["mean_max_persist"],
        }
    }, indent=2), encoding="utf-8")
    (HERE / "p5_t4r2_contact_lifecycle_analysis.json").write_text(json.dumps({
        "ablation_A_loss_calls_mean": float(np.mean([r["loss_calls"] for r in ablation_a])) if ablation_a else None,
        "ablation_B_loss_calls_mean": float(np.mean([r["loss_calls"] for r in ablation_b])) if ablation_b else None,
        "note": "Archive plant rows do not expose loss_calls. Ablation B counts one-shot applications.",
    }, indent=2), encoding="utf-8")
    failures = Counter()
    for row in proposed_a:
        if row["grasp_recovery"] and row["oracle_feasible"]:
            failures["recovered"] += 1
        elif row["safe_termination"] and not row["oracle_feasible"]:
            failures["correct_infeasibility_and_safe_stop"] += 1
        elif row["oracle_feasible"]:
            failures["controller_did_not_stabilize"] += 1
        else:
            failures["infeasible_not_safely_stopped"] += 1
    (HERE / "p5_t4r2_failure_analysis.json").write_text(json.dumps({"counts": dict(failures)}, indent=2), encoding="utf-8")
    (HERE / "p5_t4r2_reproducibility_manifest.json").write_text(json.dumps({"replay_match": replay_ok, "replay_episodes": len(replay), "selected_mode": frozen["selected_mode"]}, indent=2), encoding="utf-8")
    write_report(HERE / "p5_t4r2_scientific_evaluation_report.md", payload)
    print("VERDICT", verdict)
    print(json.dumps(summary["P5T4R2TopologyAware"][PROTOCOL_A], indent=2)[:1800])


if __name__ == "__main__":
    main()
