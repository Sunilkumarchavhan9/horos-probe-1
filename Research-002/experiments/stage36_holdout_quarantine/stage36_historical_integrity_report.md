# HOROS PROBE-1 — Research-002
## Stage 3.6 Deliverable 04: Historical Artifact & Dataset Integrity Report

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.6 — Blind Holdout Quarantine & Evaluation Integrity Recovery  
**Document Identifier:** `HOROS-P1-R002-STAGE36-REP-04-HISTORICAL-INTEGRITY`  
**Date:** 2026-11-25  
**Governance State:** `TRAINING_PERMITTED = 0` | `EXECUTION_AUTHORIZED = 0` | `NEW_HOLDOUT_GENERATION = NOT_AUTHORIZED` | `FINAL_HOLDOUT_EVALUATION = BLOCKED`  

---

### 1. Executive Summary & Verification Methodology

A critical requirement of Stage 3.6 is that the remediation of the compromised holdout dataset must **not damage, alter, move, or overwrite any valid historical engineering artifact, frozen model checkpoint, or training/validation/calibration partition**.

This report documents the bitwise and cryptographic verification confirming that all historical and newly approved Stage 3.3, 3.4, and 3.5 assets remain completely intact, unaltered, and reproducible.

---

### 2. Comprehensive Inventory of Verified Assets

| Asset Category | Location | Count / Scope | Verification Mechanism | Status |
| :--- | :--- | :--- | :--- | :---: |
| **Frozen Phase 3.4 Baselines** | `checkpoints/phase3_4/` | 37 files (B1, B2, B3, B4 checkpoints, ensembles, calibration tables) | Cryptographic SHA-256 digest match against certified Phase 3.4 manifest | **INTACT & IMMUTABLE** |
| **Stage 3.2 Certified Pilot** | `experiments/stage3_preflight/pilot/` | 40 certified pilot trajectories | Replay array equality and schema verification | **INTACT & IMMUTABLE** |
| **Stage 3.3 Production Dataset** | `experiments/stage3_production/` | 1,000 `.npz` training archives, 1,000 `.privileged.json` audit targets | SHA-256 digest verification against `accepted_manifest.json` and `stage3_3_audit_manifest.json` | **INTACT & IMMUTABLE** |
| **Stage 3.5 Prelift Long ($T=40$)** | `experiments/stage35_production/prelift_long/` | 500 `.npz` archives, 500 `.privileged.json` files | Deterministic replay verification and HMAC salt SHA-256 audit match | **INTACT & IMMUTABLE** |
| **Stage 3.4 Split Manifest** | `stage34_episode_split_manifest.json` | 1,000 episodes partitioned (700 train / 100 val / 100 calib / 100 test) | `verify_split_manifest_disjointness` assertion | **INTACT & IMMUTABLE** |
| **Stage 3.5 Final Split Manifest** | `stage35_final_split_manifest.json` | 1,620 episodes accounted (698 train / 126 val / 126 calib / 100 exploratory / 450 legacy) | `verify_split_manifest_disjointness` assertion | **INTACT & IMMUTABLE** |
| **HCTT Architectural Spec** | `src/world_model_vnext/contracts.py` | Locked 6-layer Transformer, 256 d_model, 8 heads, 32 d_head, 5.44M params | Configuration parameter assertion | **INTACT & IMMUTABLE** |
| **Locked Objective Weights** | `LOCKED_HCTT_CONFIG` | $1.0 \cdot \mathcal{L}_{\text{sim}} + 0.5 \cdot \mathcal{L}_{\text{mode}} + 0.25 \cdot \mathcal{L}_{\text{sysid}} + 0.10 \cdot \mathcal{L}_{\text{distill}}$ | Immutable dataclass assertion | **INTACT & IMMUTABLE** |
| **Training Barrier** | `src/world_model_vnext/runner.py` | `TRAINING_PERMITTED = 0`, `EXECUTION_AUTHORIZED = 0` | Strict governance constant assertion | **INTACT & LOCKED** |

---

### 3. Detailed Partition Preservation Verification

The 6 partitions defined in `experiments/stage35_production/stage35_final_split_manifest.json` were audited for zero mutation:

1. **Training Split (`train`):**
   - **Episode Count:** 698
   - **Task Composition:** 349 Compliant Prelift ($T=40$) + 349 Peg-in-Hole ($T=30$)
   - **Compromised Holdout Overlap:** Exactly 0 sample IDs, 0 seeds.
   - **Status:** Preserved intact. Ready for authorized Stage 4 student training.

2. **Validation Split (`val`):**
   - **Episode Count:** 126
   - **Task Composition:** 75 Compliant Prelift ($T=40$) + 51 Peg-in-Hole ($T=30$)
   - **Compromised Holdout Overlap:** Exactly 0 sample IDs, 0 seeds.
   - **Status:** Preserved intact. Ready for model selection.

3. **Calibration Split (`calib`):**
   - **Episode Count:** 126
   - **Task Composition:** 75 Compliant Prelift ($T=40$) + 51 Peg-in-Hole ($T=30$)
   - **Compromised Holdout Overlap:** Exactly 0 sample IDs, 0 seeds.
   - **Status:** Preserved intact. Ready for conformal quantile calibration.

4. **Quarantined Exploratory Split (`exploratory_baseline_quarantined`):**
   - **Episode Count:** 100 (Stage 3.4 evaluated test episodes)
   - **Status:** Preserved isolated. Strictly excluded from train, val, calib, and final benchmarking.

5. **Legacy Short-Horizon Split (`legacy_short_horizon_preserved`):**
   - **Episode Count:** 450 (Stage 3.3 Prelift $T=20$ episodes)
   - **Status:** Preserved historical record.

6. **Compromised Final Holdout (`final_test`):**
   - **Episode Count:** 120 (60 Prelift $T=40$ + 60 Peg-in-Hole $T=30$)
   - **Status:** **PERMANENTLY QUARANTINED.** Code guards reject all evaluation queries on this partition.

---

### 4. Git & Repository Hygiene

In strict adherence to user instructions:
- **Zero git commits** were created during Stage 3.6.
- **Zero git pushes** were executed.
- No branches were switched.
- No files were deleted or rewritten in Git history.
- All modifications are cleanly staged/unstaged for transparent user review.
