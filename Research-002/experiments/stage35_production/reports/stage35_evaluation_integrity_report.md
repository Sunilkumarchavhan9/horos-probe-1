# HOROS PROBE-1 — Research-002
## Stage 3.5 Deliverable 07: Evaluation Integrity, Quarantine & Sealed Holdout Report

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.5 — Scientific Gate Closure & HCTT-256 Training Readiness  
**Document Identifier:** `HOROS-P1-R002-STAGE35-REP-07-EVAL-INTEGRITY`  
**Date:** 2026-11-25  

---

### 1. Stage 3.4 Test-Set Exposure Audit & Quarantine

During Stage 3.4 pre-training verification (`stage34_pretraining_eval_metrics.json`), `100` Stage 3.3 episodes (`50` Compliant Prelift $T=20$, `50` Peg-in-Hole $T=30$) assigned to Stage 3.4 `test` were repeatedly inspected during pipeline debugging and baseline model benchmarking. Using those same 100 episodes or overlapping Stage 3.4 splits as a Stage 4 final holdout test set would invalidate blind statistical generalization.

Stage 3.5 permanently resolves this through `build_stage35_final_split_manifest()` (`src/world_model_vnext/splits.py`), producing `experiments/stage35_production/stage35_final_split_manifest.json` (`split_seed = 3502026`, `split_granularity = "episode_level_strict"`).

---

### 2. Six-Partition Episode Isolation Ledger (`1,620` Total Episodes)

| Partition Name | Episodes | Composition | Governance Status | Overlap With Other Splits |
| :--- | :---: | :--- | :--- | :---: |
| **`train`** | **698** | `348` Prelift ($T=40$) + `350` Peg-in-Hole ($T=30$) | Active Development | **0** |
| **`val`** | **126** | `76` Prelift ($T=40$) + `50` Peg-in-Hole ($T=30$) | Active Validation / Model Selection | **0** |
| **`calib`** | **126** | `76` Prelift ($T=40$) + `50` Peg-in-Hole ($T=30$) | Reserved Strictly for Conformal Calibration | **0** |
| **`final_test`** | **120** | `60` Prelift ($T=40$) + `60` Peg-in-Hole ($T=30$) (Fresh Seeds) | **`SEALED_UNTIL_AUTHORIZED_STAGE4_EVAL` (`sealed=True`)** | **0** |
| **`exploratory_baseline_quarantined`** | **100** | Exact 100 Stage 3.4 `test` Episodes (`50` Prelift $T=20$, `50` Peg $T=30$) | **Quarantined (`quarantined_exploratory=True`) — Excluded from Final Test** | **0** |
| **`legacy_short_horizon_preserved`** | **450** | Remaining 450 Stage 3.3 Prelift ($T=20$) Episodes | Preserved Immutable Legacy Reference | **0** |
| **Total Accounted** | **1,620** | `1,000` Stage 3.3 + `500` `prelift_long` + `120` `final_holdout` | `cross_partition_overlap_verified_zero = True` | **0** |

#### Detailed Task × Stratum Counts in Active & Sealed Splits
- **`train` (`698`):** `compliant_prelift::nominal` 174, `probe` 106, `nonstationary` 68; `peg_in_hole::nominal` 175, `impact` 105, `wedging` 70.
- **`val` (`126`):** `compliant_prelift::nominal` 38, `probe` 22, `nonstationary` 16; `peg_in_hole::nominal` 25, `impact` 15, `wedging` 10.
- **`calib` (`126`):** `compliant_prelift::nominal` 38, `probe` 22, `nonstationary` 16; `peg_in_hole::nominal` 25, `impact` 15, `wedging` 10.
- **`final_test` (`120`, SEALED):** `compliant_prelift::nominal` 30, `probe` 18, `nonstationary` 12; `peg_in_hole::nominal` 30, `impact` 18, `wedging` 12.

---

### 3. Protected Seed Pool Exclusion Verification

All `620` newly generated Stage 3.5 seeds (`prelift_long`: `3511000..3513099`; `final_holdout`: `3531000..3545011`) were checked against `STAGE35_PROTECTED_EXTRA_RANGES` and `stage3_3_seed_registry.json`:
- **300 Stage 3.3 Reserve Seeds** (`reserve_pool_locked_count = 300`, `reserve_pool_consumed = 0`): **0 overlaps**.
- **P5-T4R4 Held-Out Seeds** (`4950001..4950600` & sweep `4900000..4959999`, `p5_t4r4_heldout_overlap_count = 0`): **0 overlaps**.
- **Phase 3.4–3.5 Pools (`1000..3119`, `6000..6224`), `vnext_heldout_9200_9359`, Stage 3.2 Pilot, Stage 3.3 Production (`3311000..3325099`):** **0 overlaps**.
- **Stage 3.5 Benchmark Isolation:** `stage35_verification_metrics.json` records `final_holdout_evaluated = false` — zero model forward passes or metrics were executed on `final_holdout/`.
