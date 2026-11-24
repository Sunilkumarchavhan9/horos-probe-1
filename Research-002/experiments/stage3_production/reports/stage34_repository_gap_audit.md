# HOROS PROBE-1 — Research-002
## Stage 3.4 Deliverable 01: Repository & Evidence Gap Audit

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.4 — HCTT-256 Implementation and Temporal Training-Pipeline Completion  
**Baseline Commit Audited:** `022df850ad1fd7190f7f7afe798d3e91621a5452` (`Sunilkumarchavhan9/horos-probe-1`, branch `feat/probe1-stage33-production-readiness`)  
**Date:** 2026-11-25  

---

### 1. Executive Audit Summary

A line-by-line inspection of commit `022df85` against prior Stage 3 reports (`stage3_wm_vnext_training_readiness_report.md`, `stage3_research_register_update.md`), specifications (`horos_world_model_vnext_spec.md`, `horos_vnext_locked_training_spec.md`), and manifest (`manifests/horos_world_model_vnext_manifest.json`) identified **six critical repository and scientific gaps** requiring remediation or explicit documentation before student training can be authorized:

| # | Audited Claim / Requirement | Ground-Truth State at Commit `022df85` | Severity | Stage 3.4 Action Taken |
| :---: | :--- | :--- | :---: | :--- |
| **1** | HCTT-256 model implementation claimed prepared in `src/world_model_vnext/` | **`src/world_model_vnext/` did NOT exist** in the starting commit tree. Only `B1–B4` MLPs (`src/models/`), `ModelTrainingRunner`, and `src/teaching/` existed. | **CRITICAL** | Implemented full `src/world_model_vnext/` package (`contracts.py`, `hctt.py`, `dataset.py`, `target_loader.py`, `splits.py`, `losses.py`, `evaluator.py`, `runner.py`) and corrected Stage 3 docs. |
| **2** | Simultaneous 10-step history & 20-step future horizon | Prelift NPZs have $T=20$ steps; Peg-in-Hole NPZs have $T=30$ steps. **Prelift has 0 unpadded `(10, 20)` windows**; Peg-in-Hole has only 2/episode. | **HIGH** | Built dual-mode dataset (`masked_sliding` with explicit attention/loss masks + `strict_unpadded`) and documented exact feasibility counts. |
| **3** | Cryptographic unlinkability via HMAC `sample_id` | `STAGE3_DETERMINISTIC_AUDIT_SALT` in `scripts/run_stage3_production_dataset.py` is derived from committed source literal `b"HOROS_STAGE3_PRODUCTION_AUDIT_SALT_2026"`. | **HIGH (Security)** | Documented non-secret status openly; added runtime salt verification in `AuditTargetLoader` and defined secret-rotation migration plan without mutating the 1,000 existing NPZs. |
| **4** | Distillation loss (`beta=0.10`) using teacher representations | `Stage3ProductionTeacherController` has `has_representations=False` and `latent_representations=None`. Zero real teacher embeddings exist in Stage 3.3 data. | **MEDIUM** | Strictly gated `L_distill` on real `teacher_representations` (`0.0` with `distillation_active=False` when absent), avoiding fabricated targets while preserving locked weight `0.10`. |
| **5** | P5-T4R4 temporal replay integration fidelity | `p5_t4r4_temporal_adapter.py` uses incompatible dims ($D_s \in \{18, 22, 26\}, D_a \in \{3, 4, 5\}$) and diverged from `preload_raw_records.json` on `seed=4950001` (`grasp_success=False` vs `True` in raw log; `mass=0.5` vs `None`). | **HIGH** | Preserved P5-T4R4 artifacts untouched and excluded P5-T4R4 completely from HCTT training/evaluation until a reconciled interface is approved. |
| **6** | Baseline B1–B4 evaluation comparability & constant mass in Peg-in-Hole | Phase 3.4 Peg-in-Hole baselines trained on $\Delta t = 0.02\text{ s}$ trajectories suffer scale shift on $\Delta t = 0.01\text{ s}$ Stage 3.3 data (`H_1 MSE = 162.5–376.2`), and Peg-in-Hole `mass_kg = 0.350` is constant ($R^2$ undefined). | **MEDIUM** | Evaluated frozen B1–B3 directly on Stage 3.4 `test` split in read-only mode; returned `None` cleanly for undefined constant-mass $R^2$/correlations. |

---

### 2. Detailed Inventory at Starting Commit `022df85`

#### 2.1 Existing Reusable Components Verified Intact
- **Frozen Baselines (`src/models/`, `checkpoints/phase3_4/`):** `DeterministicWorldModel` (B1), `ProbabilisticWorldModel` (B2), `EnsembleWorldModel` (B3, 5 bootstrapped members), `ContactStratifiedConformalWM` (B4), and `FeatureStandardizer` (`normalization.py`). All checkpoints (`compliant_prelift` 23/5 `hidden_dim=64`, `peg_in_hole` 15/6 `hidden_dim=64`) verified loadable via `load_hybrid_checkpoint`.
- **Conformal Engine (`src/conformal/multihorizon_quantile_engine.py`):** Reusable $Q \in \mathbb{R}^{7 \times 5}$ quantile calibration engine across horizons `[1, 3, 5, 10, 20]`.
- **Stage 3.3 Production Dataset (`experiments/stage3_production/`):** 1,000 unmodified NPZ archives in `training/` (`500` Prelift $T=20$, `500` Peg-in-Hole $T=30$) and 1,000 `.privileged.json` files in `audit/`, indexed by `accepted_manifest.json` (`0` rejections).
- **Teaching Infrastructure (`src/teaching/`):** Complete registry, inference engine, physics validator, and experience factory; `StudentDistillationEngine` (`src/teaching/distiller.py`) handles per-episode synthetic records but lacked NPZ sliding-window ingestion, separate audit-target joining, RoPE HCTT architecture, and masked multi-horizon evaluation.

#### 2.2 Discrepancies Corrected in Documentation
Prior Stage 3 reports (`stage3_wm_vnext_training_readiness_report.md` and `stage3_research_register_update.md`) overstated implementation completeness by stating `src/world_model_vnext/` was already prepared and assembled. Both documents have been explicitly amended in Stage 3.4 to separate:
1. **Completed in Stage 3.3 (`022df85`):** Production synthetic dataset generation, dual-gate physics validation, grounded storage partitioning, and baseline readiness analysis.
2. **Completed in Stage 3.4 (this milestone):** Actual `src/world_model_vnext/` HCTT-256 model implementation, temporal sliding-window dataset, HMAC-joined `AuditTargetLoader`, episode-level split manifest, locked loss module, read-only evaluator, authorization-locked runner, and unit/integration tests.
3. **Not Yet Executed (Authorization Pending):** Student model gradient training, hyperparameter sweeps, conformal calibration on trained residuals, and post-training benchmark adjudication.
