# P5-T4R2 scientific evaluation

**Verdict: FAIL.**

Archived verdicts are unchanged. P5-T3 remains CONDITIONAL SCIENTIFIC PASS. P5-T4 remains FAIL. The P5-T4 corrective continuation remains FAIL. This run did not use hardware and did not change the certified contact kernel, the actuator limits, or those historical artifacts. Commit `512d6d1` on `horos-probe-1` is still the corrective record.

The plant timestep is 0.005 s. The slew limit is 50 N/s, so the largest force change is 0.25 N per step. The implemented clip is 0 N to 25 N. There is no 3 N floor in the plant, and none was added. One hundred plant steps is 0.500 s. Protocol A and Protocol B are reported separately. The primary baseline was selected on validation before the held-out scores were read: `preload_25`. The topology level was selected on training only: `topology_14` over `topology_10` (training feasible recovery 0.10 versus 0.033).

Held-out Protocol A uses 600 episodes: 303 oracle-feasible and 297 oracle-infeasible. Raw recovery counts below are grasp-held outcomes on that feasible set unless a different denominator is stated.

## Gateway

| Criterion | Threshold | Protocol A result |
|---|---:|---:|
| Infeasible recall | ≥95% | 169/297 = 56.90% |
| False-feasible rate | ≤5% | 11/297 = 3.70% |
| Pooled feasible recovery | ≥80% | 49/303 = 16.17% |
| N=3 recovery | ≥70% | 2/97 = 2.06% |
| N=4 recovery | ≥70% | 14/102 = 13.73% |
| N=5 recovery | ≥70% | 33/104 = 31.73% |
| Safe termination | ≥90% | 176/297 = 59.26% |
| Physical re-establishment | ≥90% of designated achievable episodes | 247/419 = 58.95% |
| Persistence after formation | 100 steps | 247/247 formed episodes reached it |
| Force, slew, budget, conservation violations | 0 | 0 |
| Replay | 100% | 20/20 seeds matched |
| P95 controller latency | ≤1 ms | 0.124 ms on the episode-p95 summary; worst step 47.03 ms |

The false-feasible rate, the zero violation counts, the replay, and the summary P95 meet their bars. Recovery, recall, safe termination, N-wise recovery, and re-establishment do not. The controller is also worse than both required baselines, so the improvement test fails.

Primary comparison, Protocol A, 303 feasible episodes, two-sided exact McNemar, bootstrap 95% interval, seed 20261124, 2000 resamples. The declared correction is Bonferroni 0.025 on the two required contrasts. Validation selected `preload_25`, so the contrast against the primary baseline and the contrast against `preload_25` are the same test.

| Contrast | Proposed only | Baseline only | Difference | 95% CI | Exact p |
|---|---:|---:|---:|---|---:|
| Topology minus matched P5-T3 | 5 | 66 | −20.13 pp | [−25.41, −15.18] | 1.19e-14 |
| Topology minus preload_25 | 0 | 72 | −23.76 pp | [−28.71, −19.14] | 4.24e-22 |
| Topology minus original P5-T4 | 39 | 16 | +7.59 pp | [+2.97, +12.22] | 0.00267 |

P5-T3 recovered 110/303. `preload_25` recovered 121/303. Original P5-T4 recovered 26/303. Topology recovered 49/303. Topology beats the original P5-T4 controller and loses to both the matched P5-T3 squeeze and the frozen preload. It never recovered a feasible episode that preload missed.

## Answers

1. **Was repeated contact loss the cause of failed re-establishment?** It is a real scheduler defect, and it is not the cause of the recovery gap. On a live grasp, the archived repeat path called the loss 40 times in 40 steps. One-shot consumption called it once, and the returned contact then held 9.5 N. On the held-out set, the same `preload_25` controller recovered 114/303 under the repeat plant and 112/303 under one-shot consumption with the original finger. Paired difference −0.66 percentage points, discordant counts 0 and 2, exact p = 0.50. The object had already exceeded the 25 mm grasp limit before a returned finger could change the outcome.

2. **Did correcting event consumption improve recovery by itself?** No. Ablation B changes only the consumption rule and the result above is a null difference. That repair is not a gain in cooperative control.

3. **Does the lifecycle machine represent the physical states?** The ten named states are implemented and the unit transitions follow loss, approach, forming, loaded, and verified. A contact returns to a loaded state only after the gap is at most 1.5 mm, the approach speed is within the finger limit, and the normal force reaches 0.30 N. Reaching the target point alone does not set the contact valid. The held-out records do not show an illegal force on an invalid contact: invalid-force violations are 0.

4. **Can a lost contact be restored without teleportation?** Yes, in the physical finger model. The finger starts on the surface, retracts 10 mm at 0.18 m/s, then approaches the current surface point at the same speed. Held-out Protocol A formed 380 contacts across 247 of 419 designated achievable episodes. The original 0.40 s and 5 mm gate remains available as the `original` finger and was not used for the proposed controller.

5. **Does the restored contact persist?** When a contact formed and no later event removed it, the episode reached at least 100 loaded steps: 247/247 formed episodes, and 380 verified contacts against 380 formations. The unit case also held one contact for at least 100 steps with a single loss application. The mean maximum persistence across all 419 achievable episodes is 76 steps because the episodes that never formed contribute zeros. A formed contact is not the same claim as a recovered grasp.

6. **Can it tell a one-shot loss from a persistent obstruction?** Yes. A one-shot event is consumed and is not applied again. A persistent obstruction stays due, blocks re-establishment, and is not rewritten into a one-shot to raise the success count. The obstruction unit test formed zero contacts and left the contact invalid.

7. **Can it handle several independent losses?** Yes. Extra `second_loss` events keep their own id and time. Held-out Protocol A event counts were 0, 1, 2, 3, or 4 on 177, 177, 134, 105, and 7 episodes. Loss applications matched those counts. A consumed event does not delete a later event.

8. **Does topology-aware recovery improve N=3?** No. Protocol A N=3 is 2/97 against P5-T3 at 10/97 and preload at 17/97.

9. **Does it improve N=4?** No. 14/102 against P5-T3 at 44/102 and preload at 47/102.

10. **Does it improve N=5?** No. 33/104 against P5-T3 at 56/104 and preload at 57/104.

11. **Does it beat matched P5-T3?** No. 49/303 versus 110/303. Difference −20.13 percentage points, p = 1.19e-14.

12. **Does it beat frozen preload_25?** No. 49/303 versus 121/303. Difference −23.76 percentage points, p = 4.24e-22. Topology won none of the discordant episodes.

13. **Does it still recognize infeasible events?** Not at the required rate. Infeasible recall is 169/297. The false-feasible rate is 11/297, which is inside 5%. False-infeasible labels are 2/303. `preload_25` on the same episodes recalls 290/297-class performance at 97.64% because its higher force changes the observation at the decision step. The topology estimator sees a lighter grasp and misses many infeasible cases.

14. **Does it keep safe degradation?** Not at the required rate. Safe stops on infeasible episodes are 176/297. The other 121 infeasible episodes are not safe stops. Failure counts are recovered 49, feasible but not stabilized 254, correct infeasible stop 176, infeasible without a safe stop 121.

15. **Does it keep the actuator limits?** Yes on the applied commands. Bound violations 0, slew violations 0, per-contact force stayed inside 0–25 N, and the peak recorded force is 25.0 N. Mean total normal force is 27.07 N, below preload's 37.16 N and below P5-T3's 46.45 N. The limits were not raised.

16. **Does it keep wrench consistency?** The logged conservation and invalid-force counters are 0 on Protocol A and Protocol B. That is the plant's existing residual check, not a new certificate.

17. **Does it generalize to unseen geometry?** The held-out seeds are disjoint from training and validation, and the stress split is separate. Stress feasible recovery is 3/29 = 10.34%, with infeasible recall 61.29% and safe termination 70.97%. Unseen geometry was not solved.

18. **Is latency inside the threshold?** The summary P95, defined as the 95th percentile of each episode's own P95, is 0.124 ms. The mean is 0.210 ms. The worst single step is 47.03 ms, which is outside 1 ms. The same summary definition was used in the corrective study. The worst step is reported here and is not counted as a pass of every control cycle.

19. **What physical limits remain?** N=3 under a 50 N/s slew and a 25 mm drop limit is still mostly unrecoverable: even preload holds only 17/97. Many failures are the object leaving the grasp before a finger can return. The physical finger under preload adds 121 versus 112 recoveries against the original finger (11 versus 2 discordant, p = 0.022). That is a small plant-level change and it does not approach 80%. The dynamic classifier still uses one bounded wrench solve for both the static and bounded-force flags, so those two terms are not independent evidence. The proposed allocator at 14 N per active contact does not put enough force on the surviving contacts.

20. **Is more simulation required before hardware?** Yes. Hardware integration is still prohibited. P5-T5 was not started.

## Ablation

Same 303 feasible episodes. `preload_25` is held fixed so the plant change is not credited to the new controller.

| Ablation | What changed | Feasible recoveries |
|---|---|---:|
| A | Repeat scheduler, preload_25 | 114/303 = 37.62% |
| B | One-shot consumption, original finger, preload_25 | 112/303 = 36.96% |
| D | One-shot consumption, physical finger, preload_25 | 121/303 = 39.93% |
| E | Physical plant plus topology_14 | 49/303 = 16.17% |

A to B is null. B to D is the finger model, about +3.0 percentage points. D to E replaces preload with the topology controller and loses 72 feasible episodes with no offsetting wins. The recovery increase from the original P5-T4 controller to topology is smaller than the increase already present in preload, and it disappears once preload is the baseline.

## Protocol B

The budget is the sum of normal forces at most `N_active × 8 N`. Violations are 0. These rows are not pooled with Protocol A.

| Controller | Feasible | Infeasible recall | False-feasible | Safe stop | Mean force |
|---|---:|---:|---:|---:|---:|
| Topology | 37/286 = 12.94% | 50.96% | 16/314 = 5.10% | 54.78% | 18.76 N |
| preload_25, budget-limited | 115/286 = 40.21% | 91.72% | 1.59% | 92.68% | 17.09 N |
| P5-T3 | 105/286 = 36.71% | no infeasible label | 0 | not a degrade policy | 20.22 N |
| Original P5-T4 | 28/286 = 9.79% | 1.27% | 6.37% | 3.50% | 4.89 N |

Protocol B false-feasible for topology is just over 5%. Preload remains ahead. Restricting preload to the budget does not make the topology controller the better policy.

## What is fixed, and what is not

Event-scheduler correctness, physical formation, and 100-step persistence after formation are separated from grasp recovery. The scheduler now consumes a one-shot event once, keeps an obstruction closed, and still fires a later event. Formed contacts can stay loaded. Grasp recovery on the feasible held-out set is 49/303. Infeasible recognition and safe stops are below their bars. The topology controller does not improve on P5-T3 or on `preload_25`.

**Recommendation: FAIL.**
