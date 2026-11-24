"""Recompute the archived P5-T4 metrics and replay a trajectory sample on the original plant."""

from __future__ import annotations

import _paths  # noqa: F401
import json
from pathlib import Path

import numpy as np

from contact_loss_scenario_generator import _draw
from feasibility_aware_recovery_controller import FeasibilityAwareRecoveryController
from p5_t4_controller_baselines import P5T3RecoveryNoFeasibility
from p5_t4_sim_env import FeasibilityGraspEnv

ARCHIVE = Path(__file__).resolve().parent.parent / "feasibility_aware_recovery_p5_t4"
HERE = Path(__file__).resolve().parent


def _rate(rows, key):
    return float(np.mean([1.0 if r[key] else 0.0 for r in rows])) if rows else None


def replay(scenario, controller_name):
    env = FeasibilityGraspEnv(scenario, "Protocol_A")
    obs = env.reset()
    if controller_name == "P5T4":
        ctrl = FeasibilityAwareRecoveryController(int(scenario["num_contacts"]), residual_mode="oppose_load", p_low=0.20, p_high=0.65, seed=int(scenario["seed"]))
    else:
        ctrl = P5T3RecoveryNoFeasibility(int(scenario["num_contacts"]))
    ctrl.reset(obs)
    trace = []
    decision = None
    while True:
        command = ctrl.step(obs)
        if decision is None and command.get("decision_label") is not None:
            decision = command["decision_label"]
        trace.append({
            "t": round(float(obs["time"]), 4),
            "sum_fn": round(float(sum(obs["fn_applied"])), 4),
            "pos": [round(float(v), 5) for v in obs["object_position"]],
            "speed": round(float(np.linalg.norm(obs["object_velocity"])), 5),
            "omega": round(float(obs["object_omega"]), 5),
            "valid": [bool(v) for v in obs["valid"]],
            "state": command.get("state"),
        })
        obs, info = env.step(command)
        if info["done"]:
            break
    return {
        "seed": int(scenario["seed"]),
        "oracle_feasible": bool(scenario["oracle_protocol_a"]),
        "grasp_held": bool(info["grasp_held"]),
        "decision": decision,
        "mean_total_fn": float(info["impulse"] / max(info["time"], 1e-6)),
        "displacement_mm": float(info["displacement_m"] * 1000.0),
        "recontact_successes": int(info["recontact_successes"]),
        "recontact_attempts": int(info["recontact_attempts"]),
        "trace_stride_4": trace[::4],
    }


def main():
    summary = json.loads((ARCHIVE / "p5_t4_evaluation_summary.json").read_text(encoding="utf-8"))
    raw = json.loads((ARCHIVE / "p5_t4_raw_records.json").read_text(encoding="utf-8"))
    proposed = [r for r in raw if r["controller"] == "P5T4FeasibilityAwareRecovery" and r["protocol"] == "Protocol_A"]
    baseline = [r for r in raw if r["controller"] == "P5T3RecoveryNoFeasibility" and r["protocol"] == "Protocol_A"]
    feasible = [r for r in proposed if r["oracle_feasible"]]
    infeasible = [r for r in proposed if not r["oracle_feasible"]]
    recomputed = {
        "episodes": len(proposed),
        "infeasible_recall": _rate(infeasible, "predicted_infeasible"),
        "false_feasible_rate": _rate(infeasible, "false_feasible"),
        "feasible_recovery": _rate(feasible, "grasp_recovery"),
        "p5t3_feasible_recovery": _rate([r for r in baseline if r["oracle_feasible"]], "grasp_recovery"),
        "safe_termination": _rate(infeasible, "safe_termination"),
        "mean_total_fn": float(np.mean([r["mean_total_fn"] for r in proposed])),
        "p5t3_mean_total_fn": float(np.mean([r["mean_total_fn"] for r in baseline])),
        "latency_p95_ms": float(np.percentile([r["latency_p95_ms"] for r in proposed], 95)),
        "mean_fn_when_recovered": float(np.mean([r["mean_total_fn"] for r in feasible if r["grasp_recovery"]])) if any(r["grasp_recovery"] for r in feasible) else None,
        "mean_fn_when_feasible_failed": float(np.mean([r["mean_total_fn"] for r in feasible if not r["grasp_recovery"]])) if any(not r["grasp_recovery"] for r in feasible) else None,
        "median_displacement_mm_feasible_fail": float(np.median([r["displacement_mm"] for r in feasible if not r["grasp_recovery"]])),
        "decision_counts": {
            label: int(sum(1 for r in proposed if r["decision_label"] == label))
            for label in ("FEASIBLE", "INFEASIBLE", "UNCERTAIN", "DEGRADED_RECOVERY", None)
        },
    }
    archived = summary["controllers"]["P5T4FeasibilityAwareRecovery"]["Protocol_A"]
    archived_base = summary["controllers"]["P5T3RecoveryNoFeasibility"]["Protocol_A"]
    checks = {
        "infeasible_recall": abs(recomputed["infeasible_recall"] - archived["classification"]["infeasible_recall"]) < 1e-12,
        "false_feasible_rate": abs(recomputed["false_feasible_rate"] - archived["classification"]["false_feasible_rate"]) < 1e-12,
        "feasible_recovery": abs(recomputed["feasible_recovery"] - archived["feasible_recovery"]) < 1e-12,
        "p5t3_feasible_recovery": abs(recomputed["p5t3_feasible_recovery"] - archived_base["feasible_recovery"]) < 1e-12,
        "safe_termination": abs(recomputed["safe_termination"] - archived["safe_termination_on_infeasible"]) < 1e-12,
        "mean_total_fn": abs(recomputed["mean_total_fn"] - archived["mean_total_fn"]) < 1e-9,
        "latency_p95_ms": abs(recomputed["latency_p95_ms"] - archived["latency_p95_ms"]) < 1e-9,
    }
    sample = []
    for n in (3, 4, 5):
        group = [r for r in feasible if r["num_contacts"] == n]
        for record in group[:4]:
            drawn = _draw(int(record["seed"]), int(record["num_contacts"]), "test")
            if drawn is not None:
                sample.append(drawn)
    trajectories = [replay(s, "P5T4") for s in sample]
    trajectories.extend(replay(s, "P5T3") for s in sample[:3])
    analysis = {
        "recomputed_from_raw_records": recomputed,
        "matches_archived_summary": checks,
        "all_checks_match": all(checks.values()),
        "plant": {"dt_s": 0.005, "slew_n_per_s": 50.0, "delta_fn_per_step": 0.25, "fn_min": 0.0, "fn_max": 25.0},
        "trajectory_sample": trajectories,
    }
    (HERE / "p5_t4_recovery_failure_analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    report = f"""# P5-T4 failure reproduction

The archived Protocol A records were recomputed without changing them. Every check against `p5_t4_evaluation_summary.json` matched: {analysis['all_checks_match']}.

| Metric | Recomputed | Archived summary |
|---|---:|---:|
| Infeasible recall | {recomputed['infeasible_recall']:.4f} | {archived['classification']['infeasible_recall']:.4f} |
| False-feasible rate | {recomputed['false_feasible_rate']:.4f} | {archived['classification']['false_feasible_rate']:.4f} |
| Feasible recovery | {recomputed['feasible_recovery']:.4f} | {archived['feasible_recovery']:.4f} |
| P5-T3 feasible recovery | {recomputed['p5t3_feasible_recovery']:.4f} | {archived_base['feasible_recovery']:.4f} |
| Safe termination | {recomputed['safe_termination']:.4f} | {archived['safe_termination_on_infeasible']:.4f} |
| Mean total force | {recomputed['mean_total_fn']:.4f} N | {archived['mean_total_fn']:.4f} N |
| P95 latency | {recomputed['latency_p95_ms']:.4f} ms | {archived['latency_p95_ms']:.4f} ms |

P5-T3 mean total force on the same episodes is {recomputed['p5t3_mean_total_fn']:.3f} N. On feasible episodes the archived controller used {recomputed['mean_fn_when_recovered']:.3f} N when it recovered and {recomputed['mean_fn_when_feasible_failed']:.3f} N when it did not. Failed feasible episodes have median peak displacement {recomputed['median_displacement_mm_feasible_fail']:.1f} mm.

The low force is the static program's minimum balance, followed by an unload when displacement passes 20 mm. It is not evidence that 4.5 N is the force required to hold the object. The comparison squeeze holds more often because it raises normal force, and therefore friction capacity, at 50 N/s. Raising the force limit is not available: the cap stays 25 N and the slew stays 50 N/s.

The archived plant steps at 5 ms, so the slew limit is 0.25 N per step. A 1 ms step is not the plant that produced these records. A trajectory sample of feasible held-out episodes was replayed on that original plant and stored in `p5_t4_recovery_failure_analysis.json`.

Decision counts: {recomputed['decision_counts']}.
"""
    (HERE / "p5_t4_failure_reproduction_report.md").write_text(report, encoding="utf-8")
    print(json.dumps({"all_checks_match": analysis["all_checks_match"], "recomputed": recomputed}, indent=2))


if __name__ == "__main__":
    main()
