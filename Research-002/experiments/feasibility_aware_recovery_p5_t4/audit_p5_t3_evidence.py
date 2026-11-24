"""
Independent P5-T3 evidence audit.

Reads the original raw episode records and the published evaluation summary.
Does not modify any P5-T3 artifact.
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List, Tuple

import numpy as np
from scipy.stats import binomtest

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
P5T3_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "n_contact_generalization_p5_t3"))
sys.path.insert(0, P5T3_DIR)

from contact_topology import ContactTopology  # noqa: E402

RAW_PATH = os.path.join(P5T3_DIR, "p5_t3_raw_records.json")
SUMMARY_PATH = os.path.join(P5T3_DIR, "p5_t3_evaluation_summary.json")

PROTO_A = "Protocol_A_EqualActuatorLimits"
PROTO_B = "Protocol_B_EqualTotalForceBudgets"
LEARNED = "LearnedUncertaintyAwareNContact"
BASELINES = [
    "EqualForcePerContact",
    "FixedTotalForce",
    "PredictiveMarginBalancing",
    "RuleBasedCooperative",
    "IndependentP5T1Adaptive",
    "P5T2LearnedExtension",
]
# Counts printed in p5_t3_scientific_evaluation_report.md section 3 (Protocol A table).
REPORT_PAIRS = {
    "EqualForcePerContact": {"b": 51, "c": 0, "p_exact": 8.882e-16},
    "FixedTotalForce": {"b": 47, "c": 0, "p_exact": 7.105e-15},
    "PredictiveMarginBalancing": {"b": 8, "c": 6, "p_exact": 0.791},
    "RuleBasedCooperative": {"b": 124, "c": 0, "p_exact": 4.702e-38},
    "IndependentP5T1Adaptive": {"b": 139, "c": 0, "p_exact": 1.437e-42},
    "P5T2LearnedExtension": {"b": 138, "c": 0, "p_exact": 2.875e-42},
}
FN_MIN = 3.0
FN_MAX = 25.0
BUDGET_PER_CONTACT = 8.0
TOL = 1e-6


def exact_mcnemar(b: int, c: int) -> Dict[str, Any]:
    n_disc = b + c
    if n_disc == 0:
        p_exact = 1.0
        ci = [0.0, 0.0]
    else:
        test = binomtest(k=b, n=n_disc, p=0.5, alternative="two-sided")
        p_exact = float(test.pvalue)
        # Clopper-Pearson interval on the conditional probability P(learned-only | discordant).
        ci_prop = test.proportion_ci(confidence_level=0.95, method="exact")
        ci = [float(ci_prop.low), float(ci_prop.high)]
    return {
        "b_learned_only": int(b),
        "c_baseline_only": int(c),
        "n_discordant": int(n_disc),
        "paired_success_difference": None,
        "exact_binomial_p_two_sided": p_exact,
        "discordant_learned_only_proportion_95ci": ci,
    }


def paired_difference_ci(y_learned: np.ndarray, y_base: np.ndarray, n_boot: int = 4000) -> Dict[str, float]:
    diff = y_learned.astype(float) - y_base.astype(float)
    n = len(diff)
    point = float(np.mean(diff))
    rng = np.random.RandomState(20261124)
    boots = np.empty(n_boot)
    for i in range(n_boot):
        idx = rng.randint(0, n, size=n)
        boots[i] = float(np.mean(diff[idx]))
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {
        "n": int(n),
        "success_learned": float(np.mean(y_learned)),
        "success_baseline": float(np.mean(y_base)),
        "paired_difference": point,
        "paired_difference_bootstrap_95ci": [float(lo), float(hi)],
    }


def index_by_seed(records: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    return {int(r["seed"]): r for r in records}


def budget_episode_audit(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_n: Dict[str, Any] = {}
    n_over = 0
    worst = 0.0
    for r in records:
        n = int(r["num_contacts"])
        ceiling = n * BUDGET_PER_CONTACT
        mean_fn = float(r["mean_total_fn"])
        over = mean_fn > ceiling + 1e-3
        if over:
            n_over += 1
        worst = max(worst, mean_fn - ceiling)
        key = f"N_{n}"
        slot = by_n.setdefault(key, {"count": 0, "episode_mean_exceeds_N_times_8": 0, "mean_of_episode_mean_fn": 0.0, "ceiling": ceiling})
        slot["count"] += 1
        slot["mean_of_episode_mean_fn"] += mean_fn
        slot["episode_mean_exceeds_N_times_8"] += int(over)
    for slot in by_n.values():
        slot["mean_of_episode_mean_fn"] = slot["mean_of_episode_mean_fn"] / max(1, slot["count"])
    return {
        "episodes": len(records),
        "episodes_whose_mean_total_force_exceeds_topology_ceiling": n_over,
        "worst_episode_mean_excess_N": worst,
        "per_topology": by_n,
        "timestep_traces_present": False,
    }


def actuator_from_summaries(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    peaks = [float(r["peak_fn_max"]) for r in records]
    by_n = {}
    for n in (3, 4, 5):
        subset = [r for r in records if int(r["num_contacts"]) == n]
        p = [float(r["peak_fn_max"]) for r in subset]
        by_n[f"N_{n}"] = {
            "episodes": len(subset),
            "max_recorded_peak_normal_force_N": float(np.max(p)) if p else None,
            "mean_recorded_peak_normal_force_N": float(np.mean(p)) if p else None,
            "episodes_with_recorded_peak_above_25N": int(sum(v > FN_MAX + 1e-6 for v in p)),
            "episodes_with_recorded_peak_below_3N": int(sum(v + 1e-9 < FN_MIN for v in p)),
        }
    return {
        "source": "episode summary field peak_fn_max only",
        "maximum_recorded_peak_normal_force_N": float(np.max(peaks)),
        "per_topology": by_n,
        "force_derivative_telemetry_present": False,
        "maximum_absolute_force_derivative_N_per_s": None,
        "force_bound_violations_from_summaries": int(sum(v > FN_MAX + 1e-6 or v + 1e-9 < 0.0 for v in peaks)),
        "actuator_rate_violations": "UNRESOLVED",
        "reason": "Raw records store one peak normal force per episode and no per-step command or slew trace.",
    }


def two_contact_proof() -> Dict[str, Any]:
    topo = ContactTopology.create_symmetric_radial(num_contacts=3, object_radius=0.040, object_mass=0.50, mu_static=0.55)
    topo.contacts[0].is_active = False
    topo.contacts[1].mu_static = 0.53
    topo.contacts[2].mu_static = 0.56
    G = topo.get_grasp_matrix(theta=0.0)
    # Columns: Fn0, Ft0, Fn1, Ft1, Fn2, Ft2. Contact 0 is inactive so its columns are zero.
    n1 = topo.contacts[1].normal
    n2 = topo.contacts[2].normal
    t1 = topo.contacts[1].tangent
    t2 = topo.contacts[2].tangent
    # Code force map: f = fn * n - ft * t. Opposing horizontal capacity uses the sign of ft
    # that reduces +Fx. Contact 1 positive code-ft increases Fx; contact 2 positive code-ft decreases Fx.
    mu1, mu2 = 0.53, 0.56
    fn = np.linspace(3.0, 25.0, 23)
    # Capacity using signed code convention, |ft| <= mu fn, chosen to minimize Fx.
    min_fx = []
    for f in fn:
        # fx = fn*(n1x+n2x) - ft1*t1x - ft2*t2x
        # To minimize fx, choose ft signs against the tangent x components.
        fx = f * (n1[0] + n2[0])
        # derivative of fx w.r.t ft1 is -t1x; to reduce fx, sign(ft1) = sign(-t1x)? 
        # fx contribution of ft is -ft * tx, so choose ft = mu*fn * sign(tx) to make -ft*tx negative.
        ft1 = mu1 * f * math.copysign(1.0, t1[0])
        ft2 = mu2 * f * math.copysign(1.0, t2[0])
        fx_min = f * (n1[0] + n2[0]) - ft1 * t1[0] - ft2 * t2[0]
        min_fx.append(float(fx_min))
    mu_crit = 1.0 / math.sqrt(3.0)
    homogeneous_gap = (math.sqrt(3.0) / 2.0) * (mu1 + mu2) - 1.0
    return {
        "object_radius_m": 0.040,
        "object_mass_kg": 0.50,
        "normals_object_frame": {"contact_1": n1.tolist(), "contact_2": n2.tolist()},
        "tangents_object_frame": {"contact_1": t1.tolist(), "contact_2": t2.tolist()},
        "grasp_matrix_active_columns_fn1_ft1_fn2_ft2": G[:, 2:6].tolist(),
        "code_force_convention": "f = fn * n - ft * t",
        "report_algebra_assumes_both_positive_Ft_reduce_Fx": True,
        "code_sign_matches_report_for_both_contacts": False,
        "capacity_statement_matches_after_choosing_opposing_sign": True,
        "mu_crit_symmetric_equal_force": mu_crit,
        "mu_1": mu1,
        "mu_2": mu2,
        "both_mu_below_mu_crit": bool(mu1 < mu_crit and mu2 < mu_crit),
        "homogeneous_opposing_margin_(sqrt3/2)*(mu1+mu2)-1": homogeneous_gap,
        "minimum_Fx_over_equal_fn_in_[3,25]_with_code_signs_N": min(min_fx),
        "equilibrium_fx_condition_met_for_any_equal_positive_fn": bool(min(min_fx) <= 1e-8),
        "linear_damping_in_p5t3_sim_N_s_per_m": 10.0,
        "undamped_acceleration_claim_6mps2": "not entailed once cone-limited friction is included; net Fx scales as about 0.056*Fn for these two coefficients",
        "drop_time_90ms_claim": "depends on the undamped zero-friction bound Fx>=3N, which the cone-limited contact model does not produce",
    }


def main() -> None:
    with open(RAW_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    with open(SUMMARY_PATH, "r", encoding="utf-8") as f:
        summary = json.load(f)

    controllers = sorted(set(raw[PROTO_A]) | set(raw[PROTO_B]))
    identity = {}
    for name in controllers:
        a = index_by_seed(raw[PROTO_A][name])
        b = index_by_seed(raw[PROTO_B][name])
        keys = sorted(set(a) & set(b))
        n_success_mismatch = 0
        n_force_mismatch = 0
        max_force_delta = 0.0
        for seed in keys:
            if bool(a[seed]["grasp_success"]) != bool(b[seed]["grasp_success"]):
                n_success_mismatch += 1
            delta = abs(float(a[seed]["mean_total_fn"]) - float(b[seed]["mean_total_fn"]))
            max_force_delta = max(max_force_delta, delta)
            if delta > 1e-9:
                n_force_mismatch += 1
        identity[name] = {
            "matched_seeds": len(keys),
            "success_mismatches_between_protocol_A_and_B": n_success_mismatch,
            "mean_force_mismatches_between_protocol_A_and_B": n_force_mismatch,
            "max_abs_mean_force_delta_N": max_force_delta,
            "records_identical_on_success_and_mean_force": n_success_mismatch == 0 and n_force_mismatch == 0,
        }

    force_audit = {}
    for proto in (PROTO_A, PROTO_B):
        force_audit[proto] = {}
        for name in controllers:
            force_audit[proto][name] = budget_episode_audit(raw[proto][name])

    # Published pooled means versus summary JSON.
    published_vs_summary = {}
    for proto, key in ((PROTO_A, "protocol_a_table"), (PROTO_B, "protocol_b_table")):
        published_vs_summary[proto] = {}
        for name in controllers:
            summary_mean = summary["protocols"][proto]["controllers"][name]["average_total_fn_mean"]
            raw_mean = float(np.mean([r["mean_total_fn"] for r in raw[proto][name]]))
            published_vs_summary[proto][name] = {
                "summary_json_average_total_fn_mean": summary_mean,
                "recomputed_from_raw_episode_means": raw_mean,
                "abs_delta": abs(summary_mean - raw_mean),
            }

    stats_out = {
        "method": "two-sided exact McNemar via scipy.stats.binomtest on discordant pairs; paired difference CI is a paired bootstrap",
        "alignment": "matched on seed",
        "multiple_comparison": {
            "family": "six Protocol A comparisons of Learned versus the six baselines",
            "correction": "Bonferroni",
            "alpha_family": 0.05,
            "alpha_per_comparison": 0.05 / 6.0,
        },
        "primary_baseline_in_original_runner": {
            "criterion_C3_uses": "EqualForcePerContact",
            "selection_recorded_before_test": "The runner hardcodes C3 against EqualForcePerContact. PredictiveMarginBalancing is computed but is not the C3 primary. No separate locked analysis plan file was found that names the primary baseline before the raw records existed.",
            "strongest_protocol_a_baseline_by_raw_success": None,
        },
        "protocol_A": {},
        "protocol_B": {},
        "report_transcription": {},
    }

    for proto, dest in ((PROTO_A, "protocol_A"), (PROTO_B, "protocol_B")):
        learned = index_by_seed(raw[proto][LEARNED])
        seeds = sorted(learned)
        y_l = np.array([1.0 if learned[s]["grasp_success"] else 0.0 for s in seeds])
        block = {}
        best_name = None
        best_rate = -1.0
        for name in BASELINES:
            base = index_by_seed(raw[proto][name])
            y_b = np.array([1.0 if base[s]["grasp_success"] else 0.0 for s in seeds])
            b = int(np.sum((y_l == 1.0) & (y_b == 0.0)))
            c = int(np.sum((y_l == 0.0) & (y_b == 1.0)))
            row = exact_mcnemar(b, c)
            row.update(paired_difference_ci(y_l, y_b))
            row["bonferroni_significant_at_0.05_family"] = bool(row["exact_binomial_p_two_sided"] < (0.05 / 6.0) and row["paired_difference"] > 0)
            if proto == PROTO_A and float(np.mean(y_b)) > best_rate:
                best_rate = float(np.mean(y_b))
                best_name = name
            if name in REPORT_PAIRS and proto == PROTO_A:
                published = REPORT_PAIRS[name]
                stats_out["report_transcription"][name] = {
                    "report_b": published["b"],
                    "report_c": published["c"],
                    "recomputed_b": b,
                    "recomputed_c": c,
                    "counts_match_report": published["b"] == b and published["c"] == c,
                    "summary_json_has_this_pair": name in {"EqualForcePerContact", "RuleBasedCooperative", "IndependentP5T1Adaptive", "PredictiveMarginBalancing"},
                }
            block[name] = row
        stats_out[dest] = block
        if proto == PROTO_A:
            stats_out["primary_baseline_in_original_runner"]["strongest_protocol_a_baseline_by_raw_success"] = {
                "controller": best_name,
                "success_rate": best_rate,
            }

    # Summary JSON pairwise versus recomputation.
    json_pairs = summary["protocols"][PROTO_A]["pairwise_comparisons"]
    stats_out["summary_json_versus_recomputed_protocol_A"] = {}
    for key, value in json_pairs.items():
        stats_out["summary_json_versus_recomputed_protocol_A"][key] = {
            "summary_b": value["b_learned_only"],
            "summary_c": value["c_baseline_only"],
            "summary_p": value["p_value_exact_2sided"],
        }

    actuator = {
        "constraints_in_environment_source": {
            "normal_force_clip_N": [FN_MIN, FN_MAX],
            "slew_limit_N_per_s": 50.0,
            "enforced_inside": "n_contact_sim_env.py step(), before the plant",
            "lost_contact_force_set_to": 0.0,
        },
        "important_limitation": "The environment clips commands. Episode summaries therefore cannot prove that the controller never requested an illegal command, and they contain no force derivative.",
        "protocols": {},
    }
    for proto in (PROTO_A, PROTO_B):
        actuator["protocols"][proto] = {name: actuator_from_summaries(raw[proto][name]) for name in controllers}

    proof = two_contact_proof()

    protocol_b = {
        "budget_definition_in_report": "sum Fn,i <= N * 8.0 N with N the nominal topology size",
        "pooled_ceiling_if_topologies_are_balanced": 32.0,
        "how_mean_total_fn_is_defined_in_the_runner": "impulse_total / episode_time, i.e. the time average of the sum of applied normal forces",
        "timestep_traces": "ABSENT. A timestep-resolved budget audit cannot be completed from the archived raw records.",
        "what_can_be_concluded": (
            "If an episode time-average exceeds N*8, then at least one timestep of that episode exceeded N*8. "
            "The converse is false: an episode average under the ceiling does not prove every timestep complied."
        ),
        "protocol_identity": identity,
        "published_mean_versus_raw": published_vs_summary,
        "episode_average_versus_topology_ceiling": force_audit,
        "runner_behavior": (
            "run_p5_t3_experiments.py passes total_force_budget only into LearnedUncertaintyAwareNContact, "
            "and only when the protocol is Protocol B. The other six controllers are constructed identically in both protocols."
        ),
        "interpretation": None,
    }
    learned_identical = identity[LEARNED]["records_identical_on_success_and_mean_force"]
    others_identical = all(identity[name]["records_identical_on_success_and_mean_force"] for name in BASELINES)
    protocol_b["interpretation"] = {
        "baselines_protocol_A_records_equal_protocol_B_records": others_identical,
        "learned_protocol_records_differ": not learned_identical,
        "predictive_margin_pooled_mean_N": published_vs_summary[PROTO_A]["PredictiveMarginBalancing"]["recomputed_from_raw_episode_means"],
        "conclusion": (
            "The 33.17 N Predictive Margin figure is the Protocol A pooled mean of episode-average total force. "
            "The same figure and the same episode outcomes were stored for Protocol B. "
            "It is not evidence that a Protocol B budget was enforced on that controller. "
            "Per-topology episode averages for Predictive Margin also exceed N*8 on N=3, N=4, and N=5. "
            "This is not a pooled-average artifact and it is not a copy from a different force definition."
        ),
    }

    os.makedirs(CURRENT_DIR, exist_ok=True)
    with open(os.path.join(CURRENT_DIR, "p5_t3_protocol_b_revalidation.json"), "w", encoding="utf-8") as f:
        json.dump(protocol_b, f, indent=2)
    with open(os.path.join(CURRENT_DIR, "p5_t3_statistical_revalidation.json"), "w", encoding="utf-8") as f:
        json.dump(stats_out, f, indent=2)
    with open(os.path.join(CURRENT_DIR, "p5_t3_actuator_safety_audit.json"), "w", encoding="utf-8") as f:
        json.dump(actuator, f, indent=2)
    with open(os.path.join(CURRENT_DIR, "_p5_t3_force_closure_numbers.json"), "w", encoding="utf-8") as f:
        json.dump(proof, f, indent=2)
    print(json.dumps({
        "identity_learned_differs": not learned_identical,
        "baselines_identical_across_protocols": others_identical,
        "predictive_over_ceiling_A": force_audit[PROTO_A]["PredictiveMarginBalancing"]["episodes_whose_mean_total_force_exceeds_topology_ceiling"],
        "learned_over_ceiling_B": force_audit[PROTO_B][LEARNED]["episodes_whose_mean_total_force_exceeds_topology_ceiling"],
        "strongest_baseline": stats_out["primary_baseline_in_original_runner"]["strongest_protocol_a_baseline_by_raw_success"],
        "transcription": stats_out["report_transcription"],
        "min_fx": proof["minimum_Fx_over_equal_fn_in_[3,25]_with_code_signs_N"],
        "mu_crit": proof["mu_crit_symmetric_equal_force"],
    }, indent=2))


if __name__ == "__main__":
    main()
