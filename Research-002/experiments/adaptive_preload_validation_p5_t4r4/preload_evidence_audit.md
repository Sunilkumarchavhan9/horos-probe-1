# HOROS PROBE-1 — Preload Evidence Audit (Stage Zero Consolidated)
## Comprehensive Audit of P4-T4B and P5-T4R2 Preload and Recovery Telemetry

**Project:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1  
**Task:** P5-T4R4 Evidence Reconciliation  

---

### 1. Overview and Objectives

This audit synthesizes the findings of `p4_t4b_evidence_audit_addendum.md` and `p5_t4r2_evidence_audit_addendum.md`, resolving all historical reporting ambiguities and establishing the empirical foundation for adaptive preload research.

### 2. Consolidated Reconciliation Table

| Research Element | Historical Reported Claim | Reconciled Empirical Ground Truth | Physical Root Cause |
|---|---|---|---|
| **P4-T4B Low-μ Hybrid Gain** | Success $0\%$ at $3\,\text{N} \to 52\%$ at $12\,\text{N}$ | Hybrid was $44\%$ at $3\,\text{N} \to 52\%$ at $12\,\text{N}$; Fixed was $0\% \to 32\%$ | Conflation in narrative between Fixed baseline and Hybrid controller |
| **P4-T4B vs Reactive Parity** | Claimed superiority / conditional pass | Exact statistical parity: $63.33\%$ vs $63.33\%$ ($p = 1.000$) | State 3 emergency recovery latched on all slips, overriding predictive State 2 |
| **P4-T4B Criterion C2** | Marked CONDITIONAL PASS | Strictly **UNMET** | Zero advantage across all 3 friction strata; $+1.3\%$ higher impulse |
| **P4-T4B Sensor Robustness** | Claimed "noise-invariance" | Recall identical at $70.83\%$ across all noise conditions | Noise invariance holds, but $29.17\%$ of slips missed under nominal noise |
| **P5-T4R2 Protocol Feasible Count** | Protocol A (303) vs Protocol B (286) | Exactly 17 episodes require total $F_n \in (8N_{\text{act}}, 25N_{\text{act}}]\,\text{N}$ | Budget in Protocol B renders 17 Protocol-A feasible episodes infeasible |
| **P5-T4R2 Recontact Metrics** | 419 designated, 380 formed, 247 episodes | 419 achievable episodes; 247 episodes formed $\ge 1$ finger; 380 total fingers formed | 143 episodes formed 1 finger, 75 formed 2, 29 formed 3 ($143+150+87=380$) |
| **P5-T4R2 Infeasible Recall** | $56.90\%$ (169/297) | 169 `INFEASIBLE`, 116 `DEGRADED_RECOVERY`, 11 `FEASIBLE`, 1 `UNCERTAIN` | Strict scoring requires exact `"INFEASIBLE"` label; degraded was excluded |
| **P5-T4R2 Safe Termination** | $59.26\%$ (176/297) | 170 `CONTROLLED_RELEASE`, 6 `SUPPORT_REST`, 121 `FAILED` drops | Infeasible episodes with degraded recovery attempts ended in drops |
| **P5-T4R2 Worst-Case Step Latency** | $47.03\,\text{ms}$ on seed `4910131` | $15$–$47\,\text{ms}$ first-solve latency on transition to `EVALUATE` | Synchronous cold-start `scipy.optimize.linprog` solve in worker process |
| **P5-T4R2 14 N Allocation Drop** | Feasible recovery dropped from $39.9\%$ to $16.2\%$ | $14\,\text{N}$ normal force gave only $4.9\,\text{N}$ friction; disturbance caused drop | Under-allocation starved surviving contacts of necessary holding friction |
| **P5-T4R2 Force-to-Recall Link** | Reduced force reduced recall | Lower force $\to$ higher velocity ($>0.08\,\text{m/s}$) $\to$ relabeled to `DEGRADED` | Kinematic state feedback causally suppressed `INFEASIBLE` label emission |

### 3. Conclusion for Adaptive Preload Validation

1. **Preload is decisive:** When initial preload is inadequate, subsequent reactive or predictive reflexes are physically bandwidth-limited ($50\,\text{N/s}$) and cannot arrest slip before drop.
2. **Brute force is wasteful:** While $25\,\text{N}$ maximizes stability, it causes massive normal force impulse and potential workpiece damage.
3. **The open research challenge:** Can pre-contact tactile probing accurately predict the minimum safe preload $F_{n,\text{safe}}$ to guarantee stability while minimizing force exposure?
