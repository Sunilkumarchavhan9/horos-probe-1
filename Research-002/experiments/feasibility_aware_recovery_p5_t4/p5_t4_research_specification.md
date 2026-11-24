# HOROS PROBE-1 — P5-T4 Research Specification

**Milestone:** Feasibility-Aware Contact Loss Recovery and Safe Degradation  
**Status:** Frozen before held-out evaluation  
**Mode:** Simulation only. Hardware integration is prohibited.  
**Date frozen:** 2026-11-24  

This file is the pre-evaluation specification. Acceptance thresholds below are not to be edited after the held-out manifest is scored.

## 1. Question

After a contact is lost, can a controller tell whether the remaining contacts still admit a wrench balance inside friction and actuator limits, recover when they do, and stop escalating force when they do not?

## 2. Isolated model

P5-T4 does not modify Simulator v1 or Contact Physics v2.1.3.11. Those kernels do not expose a constrained feasibility certificate, a support surface, or a limited-stroke recontact model. The P5-T4 plant is a separate planar rigid body:

- Object pose \((x, y, \theta)\), body-frame contacts, gravity along \(-y\).
- Contact force on the object is \(f_i = F_{n,i} n_i + F_{t,i} t_i\), with \(F_{n,i} \ge 0\) and \(|F_{t,i}| \le \mu_i F_{n,i}\).
- Tangential force is the cone-limited value closest to wrench balance at the commanded normal forces. It is a reaction, not a privileged controller input.
- Fingertip compliance is one-dimensional, \(\delta_i = F_{n,i} / k_i\), and is used for mechanical work. It does not move the rigid-body contact point.
- A lost contact can be re-established only if the controller requests it and the object surface has not moved more than 5 mm away along the outward normal since the loss.
- A support plane exists only when the scenario flag says so. It sits 15 mm below the lowest point of the object at the origin.

P5-T3 actuator limits are unchanged for gripping: each applied normal force lies in \([0, 25]\) N after release is allowed, the recovery target band used by the allocator is \([0, 25]\) N, and the slew limit is \(50\) N/s. The P5-T3 lower clip of 3 N is not imposed during degradation, because a controlled release has to unload. That exception is declared here, before evaluation. The upper bound is not raised.

Protocol B budget, evaluated on the applied normal forces at every accepted plant step:

\[
\sum_i F_{n,i} \le N_{\text{active}} \times 8.0\,\text{N}.
\]

Protocol A uses the same per-contact bound and slew limit and does not apply the total-budget projection.

## 3. Five feasibility meanings

These words are not interchangeable.

| Term | Definition used in this milestone |
|---|---|
| Geometric force closure | With normal forces unbounded above, both signs of \(F_x\), \(F_y\), and torque can be produced inside the friction cones. |
| Wrench feasibility | Some admissible contact wrench cancels the particular external wrench. |
| Bounded-actuator feasibility | A wrench solution exists with \(0 \le F_{n,i} \le 25\) N and, under Protocol B, the total-budget inequality. |
| Robust feasibility | Bounded-actuator feasibility still holds at \(\hat\mu_i - k \sigma_i\). |
| Dynamic recoverability | A bounded solution exists and the slew-limited ramp can finish before a conservative constant-acceleration estimate travels 25 mm. |

The oracle group label is bounded-actuator feasibility of the remaining contacts, computed before any controller runs. Dynamic recoverability is stored beside it and is not used to relabel the groups.

## 4. Causal information

The controller may see noisy normal and tangential force, a friction estimate and its stated uncertainty, contact validity, estimated contact geometry, estimated object pose and velocity, an estimated external wrench, actuator history, and whether a support is present in the scene.

It may not see the true friction, the true stiffness, the true stick radius, future states, or the oracle label. The friction estimate is initialized from a noisy identification draw and is updated only while that contact is slipping.

## 5. Decision rule selection

The numeric threshold is not frozen as a constant. The selection procedure is frozen:

- Training partition selects the recovery residual from a fixed candidate list, using training feasible-recovery rate only.
- Validation partition selects the Monte Carlo feasibility cutoffs \((p_{\text{low}}, p_{\text{high}})\) from the grid \(\{0.20, 0.35\} \times \{0.65, 0.80, 0.90\}\) by maximizing

\[
U = R_{\text{infeasible}} + 0.5(1 - \text{FFR}) + 0.5 S_{\text{feasible}} - 0.5 F_{\text{infeasible rate}}.
\]

Ties prefer the higher infeasible recall. The selected pair is written to the freeze file before the held-out set is scored.

- The primary comparison baseline is the non-proposed controller with the highest Protocol A feasible-recovery rate on the validation partition. Ties prefer the lower mean total normal force.

Held-out outcomes are not used to change the residual, the cutoffs, the baseline, or this specification.

## 6. Outcome definitions

**Grasp recovery success.** Final translation under 25 mm, absolute tilt under 20 degrees, speed under 0.05 m/s, at least two contacts still valid, and the terminal state is normal or recovery-verified.

**Correct infeasibility identification.** The class emitted at the feasibility evaluation is `INFEASIBLE` and the oracle group is infeasible. This is not a grasp recovery.

**Safe termination.** Used only on oracle-infeasible episodes. The terminal state is `CONTROLLED_RELEASE`, `SUPPORT_REST`, or `SAFE_HOLD`; after the first degradation entry the commanded total normal force does not rise by more than 0.25 N; actuator bounds and slew limits hold on the applied force; if the episode ends on the support, the impact speed is at most 0.35 m/s.

**False feasible.** The evaluation-time class is `FEASIBLE` while the oracle says the remaining contacts are not bounded-actuator feasible.

Infeasible episodes stay in the overall recovery denominator. They are also reported in their own denominator.

## 7. Acceptance scorecard (Stage B11)

| Criterion | Full PASS threshold |
|---|---|
| C1 Feasibility prediction | Infeasible recall at least 95% on held-out infeasible episodes. Recall counts only the label `INFEASIBLE`. |
| C2 False-feasible rate | At most 5% of held-out infeasible episodes labeled `FEASIBLE`. |
| C3 Feasible recovery | At least 80% grasp recovery on the pooled held-out feasible episodes. |
| C4 Topology robustness | At least 70% feasible-episode grasp recovery for each of N=3, N=4, and N=5. |
| C5 Safe degradation | At least 90% safe termination on held-out infeasible episodes. |
| C6 Force-budget fairness | Zero applied Protocol B budget violations. |
| C7 Physical consistency | Zero unexplained wrench-accounting or invalid-contact force violations. |
| C8 Causal telemetry | Zero runtime reads of hidden friction, hidden stiffness, future state, or oracle labels. |
| C9 Actuator safety | Zero applied force-bound or slew violations. |
| C10 Real-time execution | P95 controller-step latency at most 1.0 ms on the held-out run. |
| C11 Reproducibility | A second pass over the frozen manifest reproduces the same success bits. |

A safety-invariant failure forces the milestone to FAIL even if grasp rates look high. Conditional pass is available only for a criterion that misses its full-pass threshold while meeting the conditional floor below, and only if C6, C7, C8, and C9 pass.

| Criterion | Conditional floor |
|---|---|
| C1 | Infeasible recall at least 85%, and false-feasible rate at most 5%. |
| C3 | Pooled feasible recovery at least 65%. |
| C4 | Each topology at least 55% feasible recovery. |
| C5 | Safe termination at least 75%. |
| C10 | P95 latency at most 2.0 ms. |

C2, C6, C7, C8, C9, and C11 have no conditional floor.

## 8. Statistics

Paired binary outcomes use a two-sided exact McNemar test (`scipy.stats.binomtest` on discordant pairs). The paired success difference is reported with a percentile bootstrap interval. The held-out family is the proposed controller against each of the other eight controllers, separately for feasible recovery and for safe termination. Bonferroni uses \(\alpha = 0.05 / 8\) inside each family. Protocol A and Protocol B are separate families.

## 9. Seeds

| Partition | Seeds |
|---|---|
| Training | 4810001–4810899 |
| Validation | 4820001–4820899 |
| Held-out | 4830001–4833999 |
| Stress | 4840001–4840599 |

These ranges do not overlap production seeds, P5-T1 `4500001–4500300`, P5-T2 `4600001–4600300`, or P5-T3 `4700001–4700900`, `4710001–4710900`, and `4720001–4720300`.

## 10. Hypothesis falsifiers

- H1 is not supported if infeasible recall is below 85% or the false-feasible rate is above 10% on the held-out set.
- H2 is not supported if the proposed controller's feasible recovery does not exceed the validation-selected baseline on the held-out set.
- H3 is not supported if safe termination is below the always-squeeze recovery controller, or if post-decision force still escalates.
- H4 is not supported if the expected calibration error of the feasibility probability exceeds 0.15 on the held-out set.
- H5 is not supported if any topology's feasible recovery is below 55%, or if the stress geometries collapse to chance infeasibility detection (false-feasible rate above 20%).
