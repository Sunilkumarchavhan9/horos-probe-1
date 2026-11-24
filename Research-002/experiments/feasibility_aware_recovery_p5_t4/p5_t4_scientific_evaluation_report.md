# HOROS PROBE-1 — P5-T4 Scientific Evaluation

**Milestone:** Feasibility-aware contact-loss recovery and safe degradation  
**Mode:** Simulation only. No hardware was connected or commanded.  
**Plant:** Isolated planar Coulomb model. Contact Physics v2.1.3.11 and Simulator v1 were not modified and were not called.  
**Held-out size:** 900 scenarios (477 Protocol A feasible, 423 Protocol A infeasible). Quotas were filled with no shortfall.  
**Frozen before scoring:** residual `oppose_load` (training), cutoffs `p_low=0.20`, `p_high=0.65` (validation), primary baseline `P5T3RecoveryNoFeasibility` (validation Protocol A feasible recovery).  
**Verdict: FAIL**

The recorded P5-T3 verdict stays **CONDITIONAL SCIENTIFIC PASS**. This report does not edit that document.

## Scorecard

| Criterion | Held-out value | Status |
|---|---:|:---:|
| C1 Infeasible recall | 0.9976 | PASS |
| C2 False-feasible rate | 0.0000 | PASS |
| C3 Feasible recovery | 0.1006 | FAIL |
| C4 Minimum topology recovery (N=3) | 0.0385 | FAIL |
| C5 Safe termination on infeasible episodes | 0.9504 | PASS |
| C6 Protocol B applied budget violations | 0 | PASS |
| C7 Wrench-accounting and invalid-contact violations | 0 | PASS |
| C8 Hidden-state leakage in the observation schema | 0 forbidden keys; unit audit passed | PASS |
| C9 Applied bound and slew violations | 0 | PASS |
| C10 P95 of per-episode P95 step latency | 5.33 ms | FAIL |
| C11 Replay of Protocol A proposed grasp bits | matched | PASS |

C3 and C4 also miss the conditional floors (0.65 and 0.55). C10 misses both the 1.0 ms full threshold and the 2.0 ms conditional floor. The worst controller step was 32.4 ms, which is the Monte Carlo linear program at the decision.

## What the controller can and cannot do

On Protocol A the causal estimator labeled 99.76% of the infeasible episodes `INFEASIBLE` and labeled none of them `FEASIBLE`. The false-infeasible rate on feasible episodes was 0.84%. Brier score 0.0054. Expected calibration error 0.0063. Stress geometries, which were not used to choose the cutoffs, also had a false-feasible rate of 0.

That classification did not produce a better grasp. Feasible-episode grasp recovery was 10.1% for the proposed controller and 37.3% for the validation-selected baseline, which keeps squeezing without a feasibility test. The paired difference is −0.273 (133 episodes the baseline held and the proposed controller did not; 3 the other way; exact two-sided p = 9.6×10⁻³⁶). After Bonferroni across the eight baselines, the proposed controller is not significantly better than any of them on feasible recovery. It is worse than all seven squeeze baselines.

Per topology, feasible recovery was 3.8% (N=3), 7.5% (N=4), and 18.6% (N=5). The same ordering appears under Protocol B (5.1%, 7.6%, 18.9%). Recovery does not clear 55% on any topology.

Safe termination on infeasible episodes was 95.0%. The squeeze baselines score 0 on that metric because they never enter a degradation state; they also use far more normal force (about 20–47 N mean total, against 4.5 N for the proposed controller). Those are different outcomes. A correct infeasibility call is not counted as a recovered grasp. Overall grasp recovery, which keeps the infeasible episodes in the denominator, is 5.3%.

Protocol B applied the `N_active × 8 N` budget with zero applied violations. Applied force stayed inside `[0, 25]` N and inside the 50 N/s slew limit. Normal-force mechanical work averaged 0.0023 J. Tangential slip work averaged 0.58 J. Those are not the same quantity.

## Hypothesis disposition

| Hypothesis | Result on the held-out set |
|---|---|
| H1 Feasibility awareness | Supported for the infeasible class. Recall 99.8%, false-feasible rate 0. |
| H2 Feasibility-aware redistribution improves recovery | Not supported. The strongest validation baseline recovers more feasible episodes. |
| H3 Controlled degradation beats continued squeezing on impossible grasps | Supported for the preregistered safe-termination definition (95% versus 0% for the squeeze baselines) and for the lower normal force. |
| H4 Uncertainty calibration | Supported on this noise model. ECE 0.006 on the held-out set and a zero false-feasible rate on the stress split. |
| H5 Generalization of recovery across N and unseen geometry | Not supported. Every topology is below the 55% falsification line. Classification does carry to the stress split. |

The training residual search is the same story at smaller scale: feasible recovery was 7.3% with no residual, 3.1% with a best-friction boost, and 10.4% with the load-opposing residual. The validation cutoff grid produced the same utility for every pair, so the frozen pair is the first grid point, not a tuned improvement.

## Why recovery fails when the oracle says a balance exists

The oracle checks a static wrench at the post-event pose. The plant then has to reach the new normal forces at 50 N/s while the object is already moving. Rotational inertia of the disk is small, so a brief torque during that slew grows into a tilt the later force target does not catch. An infinite-slew replay of the same linear program can hold the easy symmetric case; the rate-limited controller does not. Four hundred two of the Protocol A non-successes are correct infeasibility calls followed by a safe stop. Three hundred eighty-five are feasible episodes the controller did not stabilize. Forty are failed recontact attempts: the finger is not allowed to return until 0.40 s have passed, and by then the surface has usually moved more than 5 mm.

## Gateway answers

1. The offline program distinguishes feasible from infeasible remaining contacts. The causal estimator matches the infeasible side closely and misses 0.84% of the feasible side by calling them infeasible.
2. Yes, for this sensor model. Infeasible recall is 99.76% with a zero false-feasible rate. Brier score 0.0054.
3. No. Feasible recovery is 10.1% against 37.3% for the selected baseline.
4. No. N=3, N=4, and N=5 feasible recovery are 3.8%, 7.5%, and 18.6%.
5. Yes, on the episodes it classifies as infeasible. Post-decision force escalation is part of the safe-termination test, which passed for 95% of infeasible episodes.
6. Yes, under the preregistered definition of safe termination. That is not a recovered grasp.
7. Yes on the applied Protocol B forces. Zero violations.
8. Yes on the applied force. Zero bound violations and zero slew violations.
9. Yes at the checks that were instrumented. Zero unexplained residual-accounting violations and zero force on an invalid contact.
10. No. It does not outperform the strongest fair baseline. The difference is in the other direction.
11. The probability is calibrated on the held-out noise model (ECE 0.006) and the stress split did not produce false-feasible calls. That is not a claim about a different sensor.
12. No. P95 latency is 5.33 ms and the worst step is 32.4 ms, because the decision solves several linear programs.
13. Static feasibility is not dynamic recoverability under a 50 N/s slew and this inertia. Lost contacts are not recreated once the object has moved. Two contacts below `1/√3` still have no positive-force balance. The plant is not Contact Physics v2.
14. Yes. Another simulation milestone is required before any hardware discussion. Hardware remains prohibited.

## Reproducibility

Seeds, oracle labels, and the frozen configuration are in `p5_t4_seed_registry.md`, `p5_t4_frozen_configuration.json`, and `p5_t4_heldout_manifest_public.json`. A second Protocol A pass of the proposed controller reproduced the same grasp bits (C11). Raw episode rows are in `p5_t4_raw_records.json`.
