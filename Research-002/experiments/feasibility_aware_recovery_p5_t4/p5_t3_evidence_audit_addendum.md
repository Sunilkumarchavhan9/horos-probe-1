# P5-T3 evidence audit addendum

**Original recorded verdict:** CONDITIONAL SCIENTIFIC PASS  
**This addendum does not replace that verdict and does not edit the original report.**  
**Source of recomputation:** `n_contact_generalization_p5_t3/p5_t3_raw_records.json` and `p5_t3_evaluation_summary.json`  
**Machine-readable companions:** `p5_t3_protocol_b_revalidation.json`, `p5_t3_statistical_revalidation.json`, `p5_t3_actuator_safety_audit.json`, `p5_t3_force_closure_proof.md`

## A. Protocol B force budget

The runner passes `total_force_budget = N * 8` only into `LearnedUncertaintyAwareNContact`, and only in Protocol B. The other six controllers are constructed the same way in both protocols.

Recomputed from the raw records:

- For all six baselines, all 900 seeds match between Protocol A and Protocol B on grasp success and on `mean_total_fn`. Protocol B did not re-simulate them under the budget.
- The learned controller does differ between protocols. Its Protocol B episode-average total force does not exceed `N * 8` on any of the 900 episodes. That is necessary but not sufficient for a timestep audit.
- Predictive Margin Balancing's pooled mean is `33.167 N` in both protocols. That is the mean of the episode-average total force. It is the same definition as the other controllers. It was not copied from a different force channel, and it is not a transcription of a single topology.
- Per topology, Predictive Margin episode averages exceed `N * 8` on 160 of 300 N=3 episodes (mean `26.81 N`, ceiling `24 N`), 127 of 300 N=4 episodes (mean `32.58 N`, ceiling `32 N`), and 62 of 300 N=5 episodes (mean `40.11 N`, ceiling `40 N`). An episode mean above the ceiling implies at least one timestep above the ceiling.
- The pooled ceiling of `32 N` is the average of `24`, `32`, and `40`. Comparing one pooled mean with that average is a different aggregation, but the per-topology excesses do not depend on it.

No per-step force trace was archived. A claim that every timestep of every controller respected the budget cannot be checked. The original report is left as published. The Protocol B table in that report repeats the Protocol A baseline numbers, which matches the identical raw records and does not measure a budget-limited baseline.

## B. Paired statistics

Recomputed with a two-sided exact McNemar test (`scipy.stats.binomtest` on discordant pairs), episodes aligned by seed. The paired difference interval is a 4000-draw percentile bootstrap. Bonferroni across the six Protocol A comparisons uses `α = 0.05/6`.

Protocol A, learned minus baseline:

| Baseline | b | c | Difference | Exact p | Report b, c | Matches the report |
|---|---:|---:|---:|---:|---|---|
| EqualForcePerContact | 51 | 0 | +0.0567 | 8.88e-16 | 51, 0 | Yes |
| FixedTotalForce | 47 | 0 | +0.0522 | 1.42e-14 | 47, 0 | Yes |
| PredictiveMarginBalancing | 7 | 5 | +0.0022 | 0.774 | 8, 6 | No |
| RuleBasedCooperative | 125 | 1 | +0.1378 | 2.99e-36 | 124, 0 | No |
| IndependentP5T1Adaptive | 140 | 1 | +0.1544 | 1.02e-40 | 139, 0 | No |
| P5T2LearnedExtension | 143 | 5 | +0.1533 | 3.21e-36 | 138, 0 | No |

The evaluation-summary JSON matches the recomputation for the four pairs it stored, including Predictive Margin `b=7, c=5, p=0.774`. The prose table does not. FixedTotal and P5T2 were not in that JSON pairwise block; their recomputed counts are above.

Predictive Margin is the strongest Protocol A baseline at 96.11% success against 96.33% for the learned controller. The matched difference is not significant. The runner's C3 comparison is hardcoded to EqualForcePerContact. No separate analysis-plan file was found that names the primary baseline before the raw records existed.

Under the stored Protocol B records, the learned controller is worse than Predictive Margin by 3.0 percentage points (exact `p = 1.43e-5`), because that baseline was not under the budget and the learned controller was. That comparison is not a fair Protocol B result.

## C. Actuator safety

The environment clips applied normal force to `[3, 25]` N while a contact is active, sets a lost contact to `0`, and limits the slew to `50 N/s`. Episode summaries store `peak_fn_max` only.

Across both protocols and all seven controllers, every archived `peak_fn_max` is at most `25 N`. The learned controller's largest archived peak is `22.69 N`. There is no per-step command trace and no force-derivative trace. The number of actuator-rate violations is **unresolved**. A report of zero rate violations is not supported by the raw telemetry. Bound compliance of the recorded peaks is consistent with the clip, not an independent audit of the pre-clip command.

## D. N=3 non-closure

See `p5_t3_force_closure_proof.md`. The equilibrium condition fails. The 25 mm / 90 ms / `6 m/s²` narrative does not match the raw episodes or the damped integrator.

## Verdict handling

The original milestone label stays **CONDITIONAL SCIENTIFIC PASS**. This addendum records that the written justifications for the Protocol B baseline comparison, the prose McNemar table, the C8 rate claim, and the 25 mm drop mechanism are not confirmed by the archived measurements. P5-T4 does not rewrite the P5-T3 report.
