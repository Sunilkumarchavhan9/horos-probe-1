# HOROS PROBE-1 — Research-002
## Stage 3.5 Deliverable 02: Long-Horizon Prelift & Blind Holdout Dataset Generation Report

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.5 — Scientific Gate Closure & HCTT-256 Training Readiness  
**Document Identifier:** `HOROS-P1-R002-STAGE35-REP-02-DATASET-GENERATION`  
**Date:** 2026-11-25  

---

### 1. Mission & Preservation Guarantee

To resolve the zero-window `(H_hist=10, H_fut=20)` feasibility gap in Stage 3.3 `compliant_prelift` ($T=20$) without mutating any archived artifact, Stage 3.5 generated two isolated versioned sub-datasets under `experiments/stage35_production/`:
1. **`prelift_long/`** — `500` Compliant Prelift scenarios at $T=40$ steps ($0.40\text{ s}$ at $\Delta t = 0.01\text{ s}$), allocated to `train` (`348`), `val` (`76`), and `calib` (`76`).
2. **`final_holdout/`** — `120` brand-new blind scenarios (`60` Compliant Prelift $T=40$ + `60` Peg-in-Hole $T=30$), sealed exclusively for post-Stage 4 final holdout evaluation (`final_test`).

All `1,000` Stage 3.3 production NPZ archives (`accepted_manifest.json` SHA-256 `45f961fce05b764968f699c9944c16c4f25c25a2a29e48a7c863c3997d51f2fd`), `stage34_episode_split_manifest.json` (`6b34cd67029bb1fae6692b82a74571801797f7b84a89130955f6e8f3c7633c2d`), the `300` Stage 3.3 reserve seeds, and all `37` frozen Phase 3.4 checkpoint files remain **100% unmodified**.

---

### 2. Allocation, Seed Provenance & Acceptance Rate

Executed via `scripts/run_stage35_pipeline.py` using `Stage3ProductionTeacherController`, `PhysicsValidationEngine.for_tasks()`, and `verify_production_physical_interaction`:

| Sub-Dataset | Task | Stratum | Length ($T$) | $\Delta t$ (s) | Seed Range | Generated | Accepted | Rejected |
| :--- | :--- | :--- | :---: | :---: | :--- | :---: | :---: | :---: |
| `prelift_long` | `compliant_prelift` | `nominal` | 40 | 0.01 | `3511000..3511249` | 250 | **250** | 0 |
| `prelift_long` | `compliant_prelift` | `probe` | 40 | 0.01 | `3512000..3512149` | 150 | **150** | 0 |
| `prelift_long` | `compliant_prelift` | `nonstationary` | 40 | 0.01 | `3513000..3513099` | 100 | **100** | 0 |
| **Subtotal `prelift_long`** | | | | | | **500** | **500 (100.0%)** | **0** |
| `final_holdout` | `compliant_prelift` | `nominal` | 40 | 0.01 | `3531000..3531029` | 30 | **30** | 0 |
| `final_holdout` | `compliant_prelift` | `probe` | 40 | 0.01 | `3532000..3532017` | 18 | **18** | 0 |
| `final_holdout` | `compliant_prelift` | `nonstationary` | 40 | 0.01 | `3533000..3533011` | 12 | **12** | 0 |
| `final_holdout` | `peg_in_hole` | `nominal` | 30 | 0.01 | `3541000..3541029` | 30 | **30** | 0 |
| `final_holdout` | `peg_in_hole` | `impact` | 30 | 0.01 | `3544000..3544017` | 18 | **18** | 0 |
| `final_holdout` | `peg_in_hole` | `wedging` | 30 | 0.01 | `3545000..3545011` | 12 | **12** | 0 |
| **Subtotal `final_holdout`** | | | | | | **120** | **120 (100.0%)** | **0** |
| **Campaign Total** | | | | | | **620** | **620 (100.0%)** | **0** |

---

### 3. Operational Contact Mode Distribution

Observed across all generated Stage 3.5 trajectories (`stage35_dataset_manifest.json`):

| Mode ID | Operational Mode | `prelift_long` Count (20,000 steps) | `final_holdout` Count (4,200 steps) |
| :---: | :--- | :---: | :---: |
| `0` | `Free` | 500 | 1,002 |
| `1` | `FirstContact` | 0 | 66 |
| `2` | `Sticking` | 12,643 | 2,285 |
| `3` | `Sliding` | 6,507 | 800 |
| `4` | `Impact` | 350 | 47 |

All `620` `.npz` training archives passed `ProductionTrainingDataloader`, `validate_model_visible_package`, and `assert_tensor_channels_clean`, and all `620` `.privileged.json` audit files passed runtime CSPRNG HMAC (`runtime_os_urandom_32bytes`) and `AuditTargetLoader` verification with `audit_salt_sha256 = 864dd3d00a67b7edfcadc9c4bd99065b49c2fffd22368bfe353af0874a45344b`.
