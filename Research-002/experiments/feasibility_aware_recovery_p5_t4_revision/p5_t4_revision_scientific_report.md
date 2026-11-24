# P5-T4 corrective scientific report

Verdict: **FAIL**.

The archived P5-T4 verdict remains FAIL. The archived P5-T3 verdict remains CONDITIONAL SCIENTIFIC PASS. Hardware was not used, and this continuation does not open P5-T5.

The archived Protocol A records were recomputed exactly: infeasible recall 99.76%, false-feasible rate 0, feasible recovery 10.06%, P5-T3 feasible recovery 37.32%, safe termination 95.04%, mean total force 4.519 N, P95 latency 5.330 ms. The plant that produced them steps at 5 ms, so the 50 N/s limit is 0.25 N per step.

## Why recovery was 10%

On the feasible episodes that failed, the archived controller still used only about 7 N of mean total normal force, against 47.3 N for the P5-T3 squeeze. Median peak displacement on those failures was 227 mm. The static program asks for the smallest balance force, and the state machine then unloads the grasp after 20 mm. A balance force is not a trajectory. During the slew the unbalanced wrench moves the object through the 25 mm boundary.

Train-only comparison, before the held-out set was scored, showed that waiting until the loss and then ramping cannot catch the object. Raising every still-valid contact toward 25 N from the first step, while the plant slews at 50 N/s, was the best of the three preloads: train feasible recovery 56.4% at 25 N, 48.9% at 16 N, and 47.9% at 12 N. The frozen choice is `preload_25`. That choice was not changed after the held-out results.

## Held-out result

Protocol A, 900 episodes, frozen preload:

| Controller | Feasible recovery | Mean total force |
|---|---:|---:|
| P5-T3 recovery, no feasibility test | 38.06% | 47.11 N |
| Corrective preload with safe stop | 37.63% | 33.70 N |
| Archived P5-T4 feasibility controller | 10.56 points below the corrective result on the paired test |  |

The paired feasible-recovery difference versus P5-T3 is -0.43 points (0 corrective-only successes, 2 baseline-only successes, exact two-sided p = 0.50). Versus the archived P5-T4 controller the difference is +27.1 points (133 vs 7, p = 2.72e-31), which passes the Bonferroni threshold of 0.00833. The corrective controller does not beat P5-T3.

By contact count the corrective rates are N=3 12.75%, N=4 39.75%, N=5 59.35%. All three are below the 70% full bar, and N=3 is below the 55% conditional floor.

Infeasible recall is 98.16%. False-feasible rate is 0.23%. Safe termination on infeasible episodes is 99.54%. Applied budget, bound, slew, and conservation violations are 0. P95 of per-episode P95 latency is 0.051 ms. The worst single step was 31.8 ms. Replay of 25 held-out episodes matched.

Protocol B, same preload under the `N_active * 8 N` cap: feasible recovery 38.08%, infeasible recall 92.24%, false-feasible rate 0.44%, safe termination 93.13%, mean force 15.77 N, budget violations 0. The recall is below the 95% bar once the cap changes which grasps are feasible.

Stress Protocol A: feasible recovery 21.4%, infeasible recall 100%, false-feasible rate 0, safe termination 100%.

Normal-force impulse and mechanical work are separate. Protocol A mean impulse is 23.59 N·s. Mean normal mechanical work is 0.0157 J. Mean tangential work is 11.64 J. The damage proxy is 54.5.

The recontact counter in the raw records (12993 successes from 210 attempts) is not a count of persistent contacts. The archived event keeps asserting the loss on later steps, so a finger that arrives is removed again on the next step and the counter increments again. Predictive recontact and the no-recontact preload differ by 0 paired feasible episodes. Contact return did not improve recovery.

## Answers

1. P5-T4 recovered 10.1% of feasible grasps because it commanded the static minimum and then unloaded, while the object was already moving.
2. Static feasibility and dynamic recoverability are different. The held-out episodes store both flags. A static balance does not keep the object inside 25 mm while the force is still slewing.
3. The actuator can reach 25 N, but only after `gap / 50` seconds. For a sudden loss that interval is long enough for the object to cross the boundary. Starting the ramp before the loss is what the train comparison selected. It still does not create a recoverable trajectory for most N=3 losses.
4. Predictive contact re-establishment did not add recoveries. The scheduled loss is re-applied every step, so the finger cannot hold a restored contact.
5. Recovery does not improve on the matched P5-T3 baseline. The difference is -0.43 points, p = 0.50.
6. Infeasible recall stays in the high 98% range on Protocol A. Protocol B recall is 92.2%.
7. Mean total force is 33.7 N, below the P5-T3 mean of 47.1 N, because infeasible episodes are released. On feasible episodes the command still rides the 25 N cap. The extra recovery over archived P5-T4 comes from that earlier, larger normal force. It does not beat the squeeze that was already using the same cap.
8. Force bounds, slew, budget, and wrench-consistency checks recorded no applied violations.
9. The P95 latency bar is met at 0.051 ms. One classification step can still take tens of milliseconds; it is not the episode P95.
10. N=3 recovery remains about 13%. Many of those objects reach the support or pass 25 mm while two contacts are still ramping. Friction collapse and tilt failures remain after the normal force has reached the cap. A higher force limit was not granted, and using the existing cap from the first step does not make those grasps recoverable.

Full bars passed: infeasible recall, false-feasible rate, safe termination, budget, conservation, causality audit, actuator limits, latency, and replay. Full bars failed: pooled feasible recovery and every contact count. The conditional floors fail on pooled recovery and on N=3. The required improvement over P5-T3 is absent.

Another simulation study would be required before any claim of recoverable contact loss. Hardware remains prohibited.
