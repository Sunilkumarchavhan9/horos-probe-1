# HOROS PROBE-1 — P4-T4B Evidence Audit Addendum
## Detailed Discrepancy Reconciliation & Recalculation from Raw Trajectories

**Project:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1  
**Milestone:** P4-T4B Evidence Reconciliation  
**Source Records:** `research/PROBE-1/Research-002/experiments/reflexive_control_p4_t4b/p4_t4b_evaluation_summary.json`  
**Reference Specification:** `p4_t4b_research_specification.md`  
**Parent Verdict:** CONDITIONAL PASS (Reconciled with explicit disclosure of Criterion C2 failure)

---

### 1. Executive Summary of Discrepancies

This audit addendum formally reconciles the experimental discrepancies identified in Phase 4 Task 4B (P4-T4B). All numbers below are recalculated directly from the 300 raw episode records.

---

### 2. Item-by-Item Reconciliation

#### Item 1 & 2: Low-Friction Preload Narrative Discrepancy (0% vs 44% at 3 N)
- **Claim in Narrative:** The executive summary claimed Hybrid success increased "from 0% at 3 N to 52% at 12 N".
- **Empirical Raw Record:**
  - **Fixed-Force Baseline:** Exactly **0.0% (0/25)** at $F_{n,0} = 3.0\,\text{N}$, increasing to **32.0% (8/25)** at $F_{n,0} = 12.0\,\text{N}$.
  - **HOROS Hybrid Controller:** Achieved **44.0% (11/25)** at $F_{n,0} = 3.0\,\text{N}$, increasing to **52.0% (13/25)** at $F_{n,0} = 12.0\,\text{N}$.
- **Resolution:** The narrative in the original summary mistakenly attributed the Fixed-Force baseline's $0\%$ baseline success rate to the Hybrid controller. The Hybrid controller achieved 44% at 3 N initial preload because State 3 emergency slip recovery actively intervened, ramping the realized normal force to an average of $14.62\,\text{N}$ (peak $19.53\,\text{N}$).

#### Item 3 & 4: Exact Parity with Reactive Slip Baseline Across All 300 Episodes
- **Empirical Raw Record:**
  - **Overall Grasp Success:** Exactly **190 / 300 (63.33%)** for **both** Reactive Slip and HOROS Hybrid.
  - **Discordant Pairs:** Exactly **0** ($b = 0, c = 0$, exact McNemar $p = 1.000$).
  - **By Friction Strata:**
    - Low Friction ($\mu_s = 0.35$, $N=100$): Reactive = **47.0%**, Hybrid = **47.0%**.
    - Moderate Friction ($\mu_s = 0.55$, $N=100$): Reactive = **60.0%**, Hybrid = **60.0%**.
    - High Friction ($\mu_s = 0.75$, $N=100$): Reactive = **83.0%**, Hybrid = **83.0%**.
  - **Impulse Exposure:** Reactive = $30.69\,\text{Ns}$, Hybrid = $31.10\,\text{Ns}$ ($+1.3\%$ for Hybrid).
  - **Workpiece Damage Proxy:** Reactive = $98.49\,\text{N}^2\text{s}$, Hybrid = $108.42\,\text{N}^2\text{s}$.
- **Physical Root Cause:** The tri-state hybrid controller's State 2 (predictive MLP reflex) commanded gentle proportional clamping. However, under high-velocity shear disturbances, the rate of partial-slip propagation exceeded the actuator slew bandwidth ($50\,\text{N/s}$), causing the grasp to transition into gross slip. The controller then immediately latched State 3 (emergency recovery), exactly mimicking the Reactive Slip baseline. Thus, the predictive reflex added zero incremental grasp recoveries.

#### Item 5: Unmet Pre-Registered Criterion C2 ("Superiority over Reactive")
- **Pre-Registered Requirement:**
  - Full PASS: Statistically significant higher success ($p < 0.05$) OR equal success with $>40\%$ lower impulse.
  - CONDITIONAL PASS: Higher success in $\ge 2$ friction strata.
- **Measured Result:** Success delta = $0.00\,\text{pp}$ across all strata; impulse was $1.3\%$ higher.
- **Audit Verdict:** Criterion C2 was **UNMET**. Labeling it CONDITIONAL PASS in the original report was an over-claim. The result is strictly **statistical parity**.

#### Item 6: 4-Quadrant Sensor Noise Robustness & 70.8% Recall
- **Source:** `p4_t3_sensor_robustness_summary.json` (24 test episodes per quadrant across 4 noise conditions).
  - **Condition A (Nominal: $\sigma_F = 0.02\,\text{N}, \sigma_\delta = 0.5\,\mu\text{m}$):** Precision = 1.000, Recall = **70.83% (17/24)**, Lead Time = $96.59\,\text{ms}$.
  - **Condition B (High Force Noise: $\sigma_F = 0.05\,\text{N}, \sigma_\delta = 0.5\,\mu\text{m}$):** Precision = 1.000, Recall = **70.83% (17/24)**, Lead Time = $96.88\,\text{ms}$.
  - **Condition C (High Disp Noise: $\sigma_F = 0.02\,\text{N}, \sigma_\delta = 1.0\,\mu\text{m}$):** Precision = 1.000, Recall = **70.83% (17/24)**, Lead Time = $96.59\,\text{ms}$.
  - **Condition D (Dual Stress: $\sigma_F = 0.05\,\text{N}, \sigma_\delta = 1.0\,\mu\text{m}$):** Precision = 1.000, Recall = **70.83% (17/24)**, Lead Time = $96.88\,\text{ms}$.
- **Resolution:** The claim of "noise-invariance" is technically correct in the narrow sense that elevated sensor noise did not degrade performance below the nominal baseline. However, the nominal baseline itself exhibited an undetected slip rate of **29.17% (7/24)**. The predictive world model was blind to impending slip in nearly 30% of scenarios before any extra noise was added.
