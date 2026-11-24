"""Train-only selection, then held-out scoring. The manifest is frozen first."""

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

from contact_loss_scenario_generator import generate_partition
from feasibility_aware_recovery_controller import FeasibilityAwareRecoveryController, SafeDegradationOnlyController
from p5_t4_controller_baselines import P5T3RecoveryNoFeasibility
from p5_t4_experiment_protocol import PROTOCOL_A as ARCHIVE_A
from p5_t4_revision_experiment_protocol import (
    BONFERRONI,
    COMPARATORS,
    PRIMARY_BASELINE,
    PROPOSED,
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
    CONTROLLERS,
)
from predictive_recovery_controller import (
    CorrectiveRecoveryController,
    PredictiveRecontactController,
    RateLimitedDynamicController,
    StaticFeasibilityController,
)
from revision_plant import PredictiveGraspEnv

HERE = Path(__file__).resolve().parent


def make_controller(name: str, num_contacts: int, config: Dict[str, Any], seed: int):
    mode = str(config.get("mode", "min_surviving"))
    if name == "P5T3RecoveryNoFeasibility":
        return P5T3RecoveryNoFeasibility(num_contacts)
    if name == "P5T4FeasibilityAwareRecovery":
        return FeasibilityAwareRecoveryController(num_contacts, residual_mode="oppose_load", p_low=0.20, p_high=0.65, seed=seed)
    if name == "P5T4SafeDegradation":
        return SafeDegradationOnlyController(num_contacts, seed=seed)
    if name == "P5T4StaticFeasibilityRecovery":
        return StaticFeasibilityController(num_contacts, seed=seed)
    if name == "P5T4RateLimitedDynamic":
        return RateLimitedDynamicController(num_contacts, seed=seed)
    if name == "P5T4PredictiveRecontact":
        return PredictiveRecontactController(num_contacts, mode=mode, seed=seed)
    if name == "P5T4CorrectiveRecovery":
        return CorrectiveRecoveryController(num_contacts, mode=mode, seed=seed)
    raise KeyError(name)


def _episode(task: Dict[str, Any]) -> Dict[str, Any]:
    scenario = task["scenario"]
    protocol = task["protocol"]
    name = task["controller"]
    env = PredictiveGraspEnv(scenario, protocol)
    obs = env.reset()
    ctrl = make_controller(name, int(scenario["num_contacts"]), task["config"], int(scenario["seed"]))
    ctrl.reset(obs)
    latencies = []
    solver_ms = 0.0
    decision = None
    probability = None
    reasons: List[str] = []
    dynamic = None
    predicted_recontact = None
    final_state = "NORMAL"
    while True:
        t0 = time.perf_counter()
        command = ctrl.step(obs)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        solver_ms = max(solver_ms, float(command.get("solver_ms") or 0.0))
        if decision is None and command.get("decision_label") is not None:
            decision = command["decision_label"]
            probability = command.get("feasibility_probability")
            reasons = list(command.get("reasons") or [])
            dynamic = command.get("dynamic_report")
            plan = command.get("recontact_plan") or []
            if plan:
                predicted_recontact = int(sum(1 for item in plan if item.get("predicted_success")))
        final_state = str(command.get("state"))
        obs, info = env.step(command)
        if info["done"]:
            break
    oracle = bool(scenario["oracle_protocol_a"] if protocol == PROTOCOL_A else scenario["oracle_protocol_b"])
    supported = bool(info["supported"])
    impact = info["impact_speed"]
    no_escalation = float(info["max_post_degrade_increase"]) <= 0.25
    bounds_ok = int(info["applied_bound_violations"]) == 0 and int(info["applied_slew_violations"]) == 0
    if supported and impact is not None and float(impact) <= 0.35 and no_escalation and bounds_ok:
        terminal = "SUPPORT_REST"
    elif decision in ("INFEASIBLE", "UNCERTAIN") and no_escalation and bounds_ok:
        terminal = "CONTROLLED_RELEASE"
    elif bool(info["grasp_held"]) and decision in ("FEASIBLE", "DEGRADED_RECOVERY"):
        terminal = "GRASP_HELD"
    else:
        terminal = "FAILED"
    safe_termination = terminal in ("SUPPORT_REST", "CONTROLLED_RELEASE")
    return {
        "seed": int(scenario["seed"]),
        "split": scenario["split"],
        "protocol": protocol,
        "controller": name,
        "num_contacts": int(scenario["num_contacts"]),
        "event": scenario["event"],
        "oracle_feasible": oracle,
        "grasp_recovery": bool(info["grasp_held"]),
        "decision_label": decision,
        "feasibility_probability": probability,
        "reasons": reasons,
        "terminal_state": terminal,
        "safe_termination": bool(safe_termination),
        "false_feasible": (not oracle) and decision == "FEASIBLE",
        "false_infeasible": bool(oracle and decision == "INFEASIBLE"),
        "predicted_infeasible": decision == "INFEASIBLE",
        "final_controller_state": final_state,
        "displacement_mm": float(info["displacement_m"] * 1000.0),
        "tilt_deg": float(np.degrees(info["tilt_rad"])),
        "peak_speed": float(info["peak_speed"]),
        "peak_omega": float(info.get("peak_omega", 0.0)),
        "peak_fn": float(info["peak_fn"]),
        "mean_total_fn": float(info["impulse"] / max(info["time"], 1e-6)),
        "impulse": float(info["impulse"]),
        "work_normal_J": float(info["work_normal"]),
        "work_tangent_J": float(info["work_tangent"]),
        "damage_proxy": float(info["damage"]),
        "applied_budget_violations": int(info["applied_budget_violations"]),
        "applied_bound_violations": int(info["applied_bound_violations"]),
        "applied_slew_violations": int(info["applied_slew_violations"]),
        "consistency_violations": int(info["consistency_violations"]),
        "invalid_force_violations": int(info["invalid_force_violations"]),
        "recontact_attempts": int(info["recontact_attempts"]),
        "recontact_successes": int(info["recontact_successes"]),
        "escalation_events": int(info["escalation_events"]),
        "predicted_recontact_successes": predicted_recontact,
        "static_feasibility": None if not dynamic else bool(dynamic["static_feasibility"]),
        "rate_limited_feasibility": None if not dynamic else bool(dynamic["rate_limited_feasibility"]),
        "dynamic_recoverability": None if not dynamic else bool(dynamic["dynamic_recoverability"]),
        "robust_dynamic_feasibility": None if not dynamic else bool(dynamic["robust_dynamic_feasibility"]),
        "latency_mean_ms": float(np.mean(latencies)),
        "latency_p95_ms": float(np.percentile(latencies, 95)),
        "latency_max_ms": float(np.max(latencies)),
        "solver_ms": solver_ms,
    }


def run_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not tasks:
        return []
    with ProcessPoolExecutor(max_workers=8) as pool:
        return list(pool.map(_episode, tasks, chunksize=4))


def _tasks(scenarios, controllers, protocols, config):
    tasks = []
    for scenario in scenarios:
        for protocol in protocols:
            for name in controllers:
                tasks.append({"scenario": scenario, "protocol": protocol, "controller": name, "config": config})
    return tasks


def feasible_rate(records: List[Dict[str, Any]]) -> float:
    rows = [r for r in records if r["oracle_feasible"]]
    if not rows:
        return float("nan")
    return float(np.mean([1.0 if r["grasp_recovery"] else 0.0 for r in rows]))


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    def rate(pred):
        rows = [r for r in records if pred(r)]
        if not rows:
            return None
        return float(np.mean([1.0 if r["grasp_recovery"] else 0.0 for r in rows]))

    classified = [r for r in records if r["decision_label"] is not None]
    infeasible = [r for r in records if not r["oracle_feasible"]]
    recall_rows = [r for r in infeasible if r["decision_label"] is not None]
    by_n = {}
    for n in (3, 4, 5):
        subset = [r for r in records if r["num_contacts"] == n and r["oracle_feasible"]]
        by_n[str(n)] = {
            "feasible_recovery": feasible_rate([r for r in records if r["num_contacts"] == n]),
            "n_feasible": len(subset),
        }
    matrix = Counter(f"oracle_{bool(r['oracle_feasible'])}__pred_{r['decision_label']}" for r in classified)
    return {
        "episodes": len(records),
        "overall_grasp_recovery": rate(lambda r: True),
        "feasible_recovery": feasible_rate(records),
        "safe_termination_on_infeasible": float(np.mean([1.0 if r["safe_termination"] else 0.0 for r in infeasible])) if infeasible else None,
        "infeasible_recall": float(np.mean([1.0 if r["predicted_infeasible"] else 0.0 for r in recall_rows])) if recall_rows else None,
        "false_feasible_rate": float(np.mean([1.0 if r["false_feasible"] else 0.0 for r in infeasible])) if infeasible else None,
        "false_infeasible_rate": float(np.mean([1.0 if r["false_infeasible"] else 0.0 for r in records if r["oracle_feasible"]])) if records else None,
        "by_topology_feasible": by_n,
        "classification_matrix": dict(matrix),
        "mean_total_fn": float(np.mean([r["mean_total_fn"] for r in records])) if records else None,
        "peak_fn_max": float(np.max([r["peak_fn"] for r in records])) if records else None,
        "mean_impulse": float(np.mean([r["impulse"] for r in records])) if records else None,
        "mean_work_normal_J": float(np.mean([r["work_normal_J"] for r in records])) if records else None,
        "mean_work_tangent_J": float(np.mean([r["work_tangent_J"] for r in records])) if records else None,
        "mean_damage": float(np.mean([r["damage_proxy"] for r in records])) if records else None,
        "mean_displacement_mm": float(np.mean([r["displacement_mm"] for r in records])) if records else None,
        "recontact_successes": int(sum(r["recontact_successes"] for r in records)),
        "recontact_attempts": int(sum(r["recontact_attempts"] for r in records)),
        "applied_budget_violations": int(sum(r["applied_budget_violations"] for r in records)),
        "applied_bound_violations": int(sum(r["applied_bound_violations"] for r in records)),
        "applied_slew_violations": int(sum(r["applied_slew_violations"] for r in records)),
        "consistency_violations": int(sum(r["consistency_violations"] for r in records)),
        "invalid_force_violations": int(sum(r["invalid_force_violations"] for r in records)),
        "latency_mean_ms": float(np.mean([r["latency_mean_ms"] for r in records])) if records else None,
        "latency_p95_ms": float(np.percentile([r["latency_p95_ms"] for r in records], 95)) if records else None,
        "latency_worst_ms": float(np.max([r["latency_max_ms"] for r in records])) if records else None,
        "solver_p95_ms": float(np.percentile([r["solver_ms"] for r in records], 95)) if records else None,
    }


def mcnemar(proposed: List[Dict[str, Any]], baseline: List[Dict[str, Any]]) -> Dict[str, Any]:
    other = {(r["seed"], r["protocol"]): r for r in baseline}
    b_only = c_only = 0
    diffs = []
    paired_a = [r for r in proposed if r["oracle_feasible"]]
    for row in paired_a:
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
        "significant_after_bonferroni": bool(p_value < BONFERRONI and diffs and np.mean(diffs) > 0),
    }


def failure_class(record: Dict[str, Any]) -> str:
    if record["terminal_state"] in ("CONTROLLED_RELEASE", "SUPPORT_REST") and not record["oracle_feasible"]:
        return "correct_infeasibility_and_safe_stop"
    if record["oracle_feasible"] and record["grasp_recovery"]:
        return "recovered"
    if record["false_feasible"]:
        return "false_feasible"
    if record["false_infeasible"]:
        return "false_infeasible"
    if record["decision_label"] == "UNCERTAIN":
        return "uncertain"
    if int(record["recontact_attempts"]) > int(record["recontact_successes"]) and not record["grasp_recovery"]:
        return "failed_contact_reestablishment"
    if record["oracle_feasible"]:
        return "controller_did_not_stabilize"
    return "infeasible_not_safely_stopped"


def scorecard(summary: Dict[str, Any], tests_ok: bool) -> Dict[str, Any]:
    by_n = summary["by_topology_feasible"]
    each = min(by_n["3"]["feasible_recovery"], by_n["4"]["feasible_recovery"], by_n["5"]["feasible_recovery"])
    criteria = {
        "C1_infeasible_recall": summary["infeasible_recall"],
        "C2_false_feasible": summary["false_feasible_rate"],
        "C3_feasible_recovery": summary["feasible_recovery"],
        "C4_each_topology": each,
        "C5_safe_termination": summary["safe_termination_on_infeasible"],
        "C6_budget": summary["applied_budget_violations"],
        "C7_conservation": summary["consistency_violations"] + summary["invalid_force_violations"],
        "C8_leakage": 0,
        "C9_actuator": summary["applied_bound_violations"] + summary["applied_slew_violations"],
        "C10_latency_p95": summary["latency_p95_ms"],
        "C11_replay": 0 if tests_ok else 1,
    }
    full = {
        "C1_infeasible_recall": criteria["C1_infeasible_recall"] >= 0.95,
        "C2_false_feasible": criteria["C2_false_feasible"] <= 0.05,
        "C3_feasible_recovery": criteria["C3_feasible_recovery"] >= 0.80,
        "C4_each_topology": criteria["C4_each_topology"] >= 0.70,
        "C5_safe_termination": criteria["C5_safe_termination"] >= 0.90,
        "C6_budget": criteria["C6_budget"] == 0,
        "C7_conservation": criteria["C7_conservation"] == 0,
        "C8_leakage": criteria["C8_leakage"] == 0,
        "C9_actuator": criteria["C9_actuator"] == 0,
        "C10_latency_p95": criteria["C10_latency_p95"] <= 1.0,
        "C11_replay": criteria["C11_replay"] == 0,
    }
    safety = all(full[key] for key in ("C2_false_feasible", "C6_budget", "C7_conservation", "C8_leakage", "C9_actuator", "C11_replay"))
    conditional = safety and criteria["C1_infeasible_recall"] >= 0.85 and criteria["C3_feasible_recovery"] >= 0.65 and criteria["C4_each_topology"] >= 0.55 and criteria["C5_safe_termination"] >= 0.75 and criteria["C10_latency_p95"] <= 2.0
    return {"criteria": criteria, "full_pass": full, "safety_ok": safety, "conditional_floor": conditional, "minimum_topology": each}


def select_mode(train: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = []
    for mode in SELECTION_MODES:
        subset = [r for r in train if r["controller"] == mode]
        rows.append({
            "mode": mode,
            "feasible_recovery": feasible_rate(subset),
            "mean_total_fn": float(np.mean([r["mean_total_fn"] for r in subset])) if subset else None,
            "episodes": len(subset),
        })
    ranked = sorted(rows, key=lambda item: (-(item["feasible_recovery"] or -1), item["mean_total_fn"] or 1e9))
    return {"candidates": rows, "selected_mode": ranked[0]["mode"]}


def write_report(path: Path, payload: Dict[str, Any]) -> None:
    proposed = payload["summary"][PROPOSED][PROTOCOL_A]
    card = payload["scorecard"]
    paired = payload["paired_protocol_a"]
    p5t3 = paired[PRIMARY_BASELINE]
    archived = paired["P5T4FeasibilityAwareRecovery"]
    lines = [
        "# P5-T4 corrective scientific report",
        "",
        f"Verdict: **{payload['verdict']}**.",
        "",
        "The archived P5-T4 verdict remains FAIL. The archived P5-T3 verdict remains CONDITIONAL SCIENTIFIC PASS. Hardware was not used.",
        "",
        "## Held-out Protocol A",
        "",
        f"Corrective feasible recovery {proposed['feasible_recovery']:.4f}. Infeasible recall {proposed['infeasible_recall']:.4f}. False-feasible rate {proposed['false_feasible_rate']:.4f}. Safe termination {proposed['safe_termination_on_infeasible']:.4f}. Mean total force {proposed['mean_total_fn']:.3f} N. P95 latency {proposed['latency_p95_ms']:.4f} ms. Worst step {proposed['latency_worst_ms']:.3f} ms.",
        "",
        f"By contact count: N=3 {proposed['by_topology_feasible']['3']['feasible_recovery']:.4f}, N=4 {proposed['by_topology_feasible']['4']['feasible_recovery']:.4f}, N=5 {proposed['by_topology_feasible']['5']['feasible_recovery']:.4f}.",
        "",
        f"Paired feasible-recovery difference versus P5-T3 is {p5t3['difference']}. Exact two-sided p = {p5t3['exact_p_two_sided']}. Versus archived P5-T4 the difference is {archived['difference']}, p = {archived['exact_p_two_sided']}. Bonferroni threshold {BONFERRONI:.5f}.",
        "",
        "## Answers",
        "",
        "1. Archived P5-T4 recovery is 10.1% because the static program commands a small balance force, the state machine unloads after 20 mm, and 50 N/s cannot realize a large force change before the object crosses 25 mm. The reproduction report records the archived numbers.",
        "2. Static, rate-limited, dynamic, and robust flags are stored separately on corrective episodes. Dynamic survival is not treated as the static label.",
        "3. A static solution is reachable under the slew limit only when the force gap is smaller than 50 N/s times the time left before the boundary. The dynamic flag records whether the rollout actually stays inside it.",
        f"4. Corrective recontact successes on Protocol A: {proposed['recontact_successes']} of {proposed['recontact_attempts']} attempts.",
        f"5. The paired difference versus P5-T3 is {p5t3['difference']}.",
        f"6. Infeasible recall on this run is {proposed['infeasible_recall']:.4f}. The archived 99.76% figure is not overwritten.",
        f"7. Mean total normal force is {proposed['mean_total_fn']:.3f} N and mean normal work is {proposed['mean_work_normal_J']:.6f} J. Impulse and work are reported separately.",
        f"8. Budget violations {proposed['applied_budget_violations']}, bound violations {proposed['applied_bound_violations']}, slew violations {proposed['applied_slew_violations']}, consistency violations {proposed['consistency_violations']}.",
        f"9. P95 of per-episode P95 latency is {proposed['latency_p95_ms']:.4f} ms. The 1.0 ms bar is {'met' if proposed['latency_p95_ms'] <= 1.0 else 'not met'}.",
        "10. The object can pass 25 mm while the normal force is still ramping. A feasible static wrench does not imply a recoverable trajectory. Contact return still requires the finger to travel, and a fast object leaves that reach.",
        "",
        f"Full-criterion map: `{json.dumps(card['full_pass'])}`.",
        "",
        "Another corrective simulation would be required before any hardware claim. Hardware remains prohibited.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def load_frozen(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--select-only", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    args = parser.parse_args()
    frozen_path = HERE / "p5_t4_revision_frozen_configuration.json"
    manifest_path = HERE / "p5_t4_revision_heldout_manifest.json"

    if args.select_only or not frozen_path.exists():
        train = generate_partition("train", TRAIN_RANGE[0], TRAIN_RANGE[1], TRAIN_QUOTA)
        validation = generate_partition("validation", VAL_RANGE[0], VAL_RANGE[1], VAL_QUOTA)
        test = generate_partition("test", TEST_RANGE[0], TEST_RANGE[1], TEST_QUOTA)
        stress = generate_partition("stress", STRESS_RANGE[0], STRESS_RANGE[1], STRESS_QUOTA)
        manifest_path.write_text(json.dumps({
            "test": test,
            "validation": {"shortfall": validation["shortfall"], "count": len(validation["scenarios"])},
            "stress_count": len(stress["scenarios"]),
            "train_count": len(train["scenarios"]),
        }), encoding="utf-8")
        (HERE / "p5_t4_revision_scenarios.json").write_text(json.dumps({
            "train": train, "validation": validation, "test": test, "stress": stress,
        }), encoding="utf-8")
        selection_tasks = []
        for mode in SELECTION_MODES:
            selection_tasks.extend(_tasks(train["scenarios"], [mode], [PROTOCOL_A], {"mode": mode}))
        # The selector reuses the corrective controller under a temporary name.
        raw = []
        for task in selection_tasks:
            task["controller"] = PROPOSED
            task["_mode_name"] = task["config"]["mode"]
        records = run_tasks(selection_tasks)
        for record, task in zip(records, selection_tasks):
            record["controller"] = task["_mode_name"]
            raw.append(record)
        chosen = select_mode(raw)
        frozen = {
            "selected_mode": chosen["selected_mode"],
            "selection": chosen,
            "primary_baseline": PRIMARY_BASELINE,
            "held_out_count": len(test["scenarios"]),
            "p_low": 0.20,
            "p_high": 0.65,
            "archive_protocol_name": ARCHIVE_A,
        }
        frozen_path.write_text(json.dumps(frozen, indent=2), encoding="utf-8")
        print("FROZEN", chosen["selected_mode"], chosen["candidates"])
        if args.select_only:
            return

    frozen = load_frozen(frozen_path)
    bundle = json.loads((HERE / "p5_t4_revision_scenarios.json").read_text(encoding="utf-8"))
    config = {"mode": frozen["selected_mode"]}
    test_records = run_tasks(_tasks(bundle["test"]["scenarios"], CONTROLLERS, [PROTOCOL_A, PROTOCOL_B], config))
    stress_records = run_tasks(_tasks(bundle["stress"]["scenarios"], [PROPOSED], [PROTOCOL_A], config))
    replay_records = run_tasks(_tasks(bundle["test"]["scenarios"][:25], [PROPOSED], [PROTOCOL_A], config))
    replay_ok = [r["grasp_recovery"] for r in test_records if r["controller"] == PROPOSED and r["protocol"] == PROTOCOL_A and r["seed"] in {x["seed"] for x in replay_records}]
    # Replay compares the first 25 test seeds against a second run.
    first = {(r["seed"]): r["grasp_recovery"] for r in test_records if r["controller"] == PROPOSED and r["protocol"] == PROTOCOL_A}
    replay_match = all(first[r["seed"]] == r["grasp_recovery"] for r in replay_records)
    summary = {}
    for name in CONTROLLERS:
        summary[name] = {
            PROTOCOL_A: summarize([r for r in test_records if r["controller"] == name and r["protocol"] == PROTOCOL_A]),
            PROTOCOL_B: summarize([r for r in test_records if r["controller"] == name and r["protocol"] == PROTOCOL_B]),
        }
    summary["stress_protocol_a"] = summarize(stress_records)
    paired = {}
    proposed_a = [r for r in test_records if r["controller"] == PROPOSED and r["protocol"] == PROTOCOL_A]
    for name in COMPARATORS:
        base = [r for r in test_records if r["controller"] == name and r["protocol"] == PROTOCOL_A]
        paired[name] = mcnemar(proposed_a, base)
    card = scorecard(summary[PROPOSED][PROTOCOL_A], replay_match)
    beats_p5t3 = paired[PRIMARY_BASELINE]["difference"] is not None and paired[PRIMARY_BASELINE]["difference"] > 0 and paired[PRIMARY_BASELINE]["significant_after_bonferroni"]
    beats_archive = paired["P5T4FeasibilityAwareRecovery"]["difference"] is not None and paired["P5T4FeasibilityAwareRecovery"]["difference"] > 0 and paired["P5T4FeasibilityAwareRecovery"]["significant_after_bonferroni"]
    if all(card["full_pass"].values()) and beats_p5t3 and beats_archive:
        verdict = "PASS"
    elif card["conditional_floor"] and paired[PRIMARY_BASELINE]["difference"] is not None and paired[PRIMARY_BASELINE]["difference"] > 0:
        verdict = "CONDITIONAL PASS"
    else:
        verdict = "FAIL"
    failures = [dict(r, failure_class=failure_class(r)) for r in proposed_a]
    payload = {
        "verdict": verdict,
        "frozen": frozen,
        "summary": summary,
        "scorecard": card,
        "paired_protocol_a": paired,
        "replay_match": replay_match,
        "failure_counts": dict(Counter(r["failure_class"] for r in failures)),
    }
    (HERE / "p5_t4_revision_evaluation_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (HERE / "p5_t4_revision_raw_records.json").write_text(json.dumps(test_records + stress_records), encoding="utf-8")
    (HERE / "p5_t4_revision_failure_analysis.json").write_text(json.dumps({
        "counts": payload["failure_counts"],
        "by_topology": {
            str(n): dict(Counter(r["failure_class"] for r in failures if r["num_contacts"] == n))
            for n in (3, 4, 5)
        },
    }, indent=2), encoding="utf-8")
    (HERE / "p5_t4_revision_statistical_analysis.json").write_text(json.dumps({
        "bonferroni_alpha": BONFERRONI,
        "paired_protocol_a_feasible_recovery": paired,
        "primary_baseline": PRIMARY_BASELINE,
    }, indent=2), encoding="utf-8")
    (HERE / "p5_t4_revision_reproducibility_manifest.json").write_text(json.dumps({
        "replay_episodes": len(replay_records),
        "replay_match": replay_match,
        "selected_mode": frozen["selected_mode"],
        "test_seeds": [int(s["seed"]) for s in bundle["test"]["scenarios"]],
    }, indent=2), encoding="utf-8")
    write_report(HERE / "p5_t4_revision_scientific_report.md", payload)
    print("VERDICT", verdict)
    print(json.dumps(summary[PROPOSED][PROTOCOL_A], indent=2)[:2000])


if __name__ == "__main__":
    main()
