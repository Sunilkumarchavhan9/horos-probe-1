# P5-T4 corrective continuation

This is a corrective continuation of P5-T4. It does not open P5-T5. The archived P5-T4 verdict stays FAIL. The archived P5-T3 verdict stays CONDITIONAL SCIENTIFIC PASS.

Hardware is prohibited. The plant is the archived P5-T4 planar grasp model: `dt = 0.005 s`, normal force in `[0, 25] N`, slew `50 N/s` (`0.25 N` per step). The corrective note that quotes a 1 ms step would replace that plant. This continuation keeps the archived step.

## Question

Can the controller decide that a contact-loss recovery is dynamically reachable under the slew limit, friction, geometry, and object motion, and execute that recovery before the 25 mm / 20 degree boundary?

## Decisions

The tested controller still emits only `FEASIBLE`, `INFEASIBLE`, or `UNCERTAIN` (plus the archived `DEGRADED_RECOVERY` label when a feasible grasp is already moving). A feasible grasp that is not recovered is not relabeled infeasible after the outcome.

## Four feasibility terms

These are separate booleans:

- Static feasibility: a bounded-actuator wrench balance exists.
- Rate-limited feasibility: that force vector is reachable from the current force under 50 N/s before the estimated time-to-boundary.
- Dynamic recoverability: a rollout on the plant's contact reaction and integrator stays inside the boundary.
- Robust dynamic feasibility: the same rollout still survives with lower friction and a larger external wrench.

## Seeds

| Split | Inclusive start | Exclusive stop |
|---|---:|---:|
| Train | 4850001 | 4852501 |
| Validation | 4860001 | 4862501 |
| Held-out | 4870001 | 4876001 |
| Stress | 4880001 | 4881501 |

These ranges do not overlap P5-T1 `4500001–4500300`, P5-T2 `4600001–4600300`, P5-T3 `4700001–4720300`, or P5-T4 `4810001–4840600`.

## Selection

Primary baseline, declared before held-out scoring: `P5T3RecoveryNoFeasibility`.

The post-loss static allocation cannot catch the object, because 50 N/s does not deliver the missing normal force before the 25 mm boundary. Train-only selection therefore compares three preloads that start rising on the first causal step, before the contact is lost: `preload_12`, `preload_16`, and `preload_25` newtons per valid contact. The plant still slews at 50 N/s and still clips at 25 N. The winner is the highest Protocol A feasible-recovery rate. Ties break toward the lower mean total normal force. The held-out manifest is written before this selection, and the selection file is written before any held-out controller is scored.

## Acceptance

Original P5-T4 thresholds stay in force. A full PASS also requires the corrective controller to beat both `P5T3RecoveryNoFeasibility` and `P5T4FeasibilityAwareRecovery` on paired feasible recovery under Protocol A, with a two-sided exact McNemar test significant after Bonferroni correction across the six comparators (`alpha = 0.05 / 6`).

Conditional floors, and only if force-budget, conservation, causality, actuator, and replay checks pass:

| Criterion | Conditional floor |
|---|---:|
| Infeasible recall | ≥85% and false-feasible rate ≤5% |
| Pooled feasible recovery | ≥65% |
| Each of N=3, 4, 5 | ≥55% |
| Safe termination on infeasible | ≥75% |
| P95 of per-episode P95 latency | ≤2.0 ms |

False-feasible rate, force budget, conservation, causality, actuator bounds, slew, and replay have no conditional floor. Missing the paired improvement against P5-T3 forces FAIL even if a conditional floor is met.

## Protocols

Protocol A uses the same actuator limits for every controller. Protocol B enforces `sum Fn <= N_active * 8 N` on the applied command at every step. The protocols are reported separately.
