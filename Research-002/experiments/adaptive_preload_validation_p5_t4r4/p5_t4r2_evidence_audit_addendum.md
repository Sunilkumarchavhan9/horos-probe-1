# HOROS PROBE-1 — P5-T4R2 Evidence Audit Addendum
## Detailed Discrepancy Reconciliation & Raw Trajectory Analysis

**Project:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1  
**Milestone:** P5-T4R2 Evidence Reconciliation  
**Source Records:** `research/PROBE-1/Research-002/experiments/persistent_contact_recovery_p5_t4r2/p5_t4r2_raw_records.json`  
**Summary:** `p5_t4r2_evaluation_summary.json`  
**Parent Verdict:** FAIL

---

### 1. Executive Summary

This addendum answers the eight specific empirical discrepancies and mathematical questions raised regarding the P5-T4R2 benchmark. All statistics are recomputed from the 600 held-out test episodes.

---

### 2. Item-by-Item Reconciliation

#### Item 1: Protocol A (303 Feasible) vs Protocol B (286 Feasible) Denominator Difference
- **Protocol A:** Actuator bounds $F_{n,i} \in [0, 25.0]\,\text{N}$, slew $\le 50.0\,\text{N/s}$, **no total force budget**. Oracle feasible = **303 / 600 episodes**.
- **Protocol B:** Adds total-force budget $\sum F_{n,i} \le N_{\text{active}} \times 8.0\,\text{N}$. Oracle feasible = **286 / 600 episodes**.
- **The 17 Discordant Episodes:** In exactly 17 test episodes, static wrench equilibrium and friction cone satisfaction require a total normal clamping force $\sum F_n \in (N_{\text{active}} \times 8.0\,\text{N}, N_{\text{active}} \times 25.0\,\text{N}]$. These episodes are solvable under Protocol A, but mathematically impossible under Protocol B's budget.
- **Verification:** All 286 Protocol B feasible episodes are a strict subset of Protocol A feasible episodes ($A_{\text{feas}} \cap B_{\text{feas}} = 286$, $B_{\text{feas}} \setminus A_{\text{feas}} = 0$).

#### Item 2: 419 Designated Opportunities, 380 Formed Contacts, and 247 Formation Episodes
- **419 Achievable Episodes:** Subsets of the 600 test episodes where contact loss occurred, the disturbance was not friction collapse/gradual, and the workpiece was kinematically reachable ($d \le 0.45 \times v_{\text{finger}}$).
- **247 Formation Episodes:** Distinct test episodes in which at least one lost finger completed retraction, approached, closed the gap to $\le 1.5\,\text{mm}$, and achieved normal force $\ge 0.30\,\text{N}$ ($247 / 419 = 58.95\%$).
- **380 Formed Contacts:** The total count of individual contacts formed across those 247 episodes:
  - 143 episodes formed exactly 1 contact ($143 \times 1 = 143$).
  - 75 episodes formed exactly 2 contacts ($75 \times 2 = 150$).
  - 29 episodes formed exactly 3 contacts ($29 \times 3 = 87$).
  - Total individual formations = $143 + 150 + 87 = \mathbf{380}$.

#### Item 3: Complete Classification Confusion Matrix
Evaluated on the 600 held-out test episodes:

| True Oracle State | Predicted `FEASIBLE` | Predicted `DEGRADED_RECOVERY` | Predicted `INFEASIBLE` | Predicted `UNCERTAIN` | Total Episodes |
|---|:---:|:---:|:---:|:---:|:---:|
| **Oracle FEASIBLE (Protocol A)** | 288 | 13 | 2 | 0 | **303** |
| **Oracle INFEASIBLE (Protocol A)** | 11 | 116 | 169 | 1 | **297** |
| **Oracle FEASIBLE (Protocol B)** | 260 | 25 | 0 | 1 | **286** |
| **Oracle INFEASIBLE (Protocol B)** | 16 | 132 | 160 | 6 | **314** |

#### Item 4: Definitions and Denominators of Infeasible Recall and False-Feasible Rate
- **Infeasible Recall ($56.90\%$):**
  - **Denominator:** Exactly the **297** oracle-infeasible episodes.
  - **Numerator:** Exactly the **169** episodes where `decision_label == "INFEASIBLE"`.
  - **Impact of `DEGRADED_RECOVERY`:** 116 infeasible episodes were classified as `DEGRADED_RECOVERY`. Under pre-registered strict scoring, these do not count as predicting infeasibility, dropping recall to $169 / 297 = 56.90\%$.
- **False-Feasible Rate ($3.70\%$):**
  - **Denominator:** Exactly the **297** oracle-infeasible episodes.
  - **Numerator:** Exactly the **11** episodes where `decision_label == "FEASIBLE"`.
  - **Rate:** $11 / 297 = 3.70\% \le 5.0\%$ (**PASS**).

#### Item 5: Safe-Termination Denominator
- **Denominator:** Exactly all **297** oracle-infeasible episodes.
- **Numerator:** Episodes reaching `CONTROLLED_RELEASE` (170) or `SUPPORT_REST` (6) = **176**.
- **Rate:** $176 / 297 = 59.26\%$.
- **Failed Infeasible Episodes:** 121 infeasible episodes ended in `FAILED` drops because the controller attempted recovery rather than executing safe termination.

#### Item 6: The 47.03 ms Worst-Case Latency Step
- **Observed Episode:** Seed `4910131` (Protocol A, $N=3$, `multi_loss`). Max step latency = $47.025\,\text{ms}$.
- **Root Cause:** Occurs on the transition into `EVALUATE`, where `CausalFeasibilityEstimator.estimate()` and `solve_bounded_wrench()` trigger `scipy.optimize.linprog` initialization and linear program solving.
- Every episode experienced a first-solve latency between $15\,\text{ms}$ and $47\,\text{ms}$, while subsequent non-eval steps averaged $0.06$–$0.12\,\text{ms}$ (P95 = $0.124\,\text{ms}$).

#### Item 7 & 8: 14 N Allocation Policy and Causal Degradation of Infeasibility Recall
- **Recovery Impact:** In P5-T4 revision, `preload_25` applied $25\,\text{N}$, providing $\mu F_n \approx 0.35 \times 25 = 8.75\,\text{N}$ friction capacity per contact. Under `topology_14`, force was capped at $14\,\text{N}$ ($\mu F_n \approx 4.9\,\text{N}$), allowing immediate acceleration past the $25\,\text{mm}$ drop limit ($16.17\%$ vs $39.93\%$).
- **Causal Connection to Infeasibility Recall:** The lower force directly caused higher object velocity ($v > 0.08\,\text{m/s}$) at the decision step. Line 85 of `feasibility_estimator.py` overrides `label = "DEGRADED_RECOVERY"` when `speed > 0.08`. Under $25\,\text{N}$, velocity was suppressed ($v \le 0.08\,\text{m/s}$), allowing the estimator to emit `INFEASIBLE` directly ($97.64\%$ recall). Thus, reduced normal force **causally caused** the drop in infeasibility recall through dynamic kinematic feedback.
