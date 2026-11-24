# HOROS PROBE-1 — Research-002
## Stage 3 Deliverable 10: Research Register & Program State Update

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.5 — Scientific Gate Closure & HCTT-256 Training Readiness  
**Document Identifier:** `HOROS-P1-R002-STAGE3-REP-10-RESEARCH-REGISTER`  
**Date:** 2026-11-25  
**Current Milestone:** **Stage 3.5 Complete (Scientific & Data-Integrity Gates Closed; HCTT-256 Training-Ready, Untrained)**  
**Next Milestone:** **HOROS Stage 4 — First Authorized HCTT-256 Training Experiment** *(Locked)*  
**Governance Status:** **TRAINING LOCKED (`training_permitted = 0`, `execution_authorized = 0`, `gradient_steps = 0`, `optimizer_updates = 0`) — AWAITING FORMAL USER AUTHORIZATION**

---

### 1. Research Register Summary Table

| Milestone / Sub-Task | Status | Evidence Artifact | Scientific Verdict / Key Outcome |
| :--- | :---: | :--- | :--- |
| **Phase 3.4** | **CERTIFIED** | `checkpoints/phase3_4/` | Trained frozen baselines B1 (Det. MLP), B2 (Prob. MLP), B3 (Ensemble), B4 (Conformal). |
| **Phase 3.5** | **CERTIFIED** | `experiments/phase3_5_evaluation/` | Offline (120 ep) and closed-loop (450 ep) evaluation completed. |
| **Phase 3.6** | **CERTIFIED** | `experiments/phase3_6_robustness_validation/` | Robustness campaign: 1,215 episodes evaluated across M4_acmm / M0_passive. Zero crush violations. |
| **Phase 3.7** | **CERTIFIED** | `experiments/phase3_7_uncertainty_intelligence/` | Trust region diagnostics, causal interaction histories, active dwell investigation. |
| **P5-T4R4** | **ISOLATED (ARCHIVED)** | `scripts/p5_t4r4_temporal_adapter.py` | Scalar record audit complete; replay adapter incompatible with HCTT dims and diverged on `seed=4950001` (`grasp_success` False vs True). Kept separate. |
| **Stage 3.2** | **CERTIFIED** | `experiments/stage3_preflight/pilot/` | 40-episode physical pilot accepted with causal Regime A slip and event-aligned rim impact. |
| **Stage 3.3 Production Dataset** | **COMPLETED (`022df85`)** | `experiments/stage3_production/` | 1,000 / 1,000 scenarios generated and 100% accepted (500 Prelift, 500 Peg-in-Hole). Zero rejections; HCTT code not yet present at `022df85`. |
| **Stage 3.4 HCTT & Pipeline** | **COMPLETED (`6d890eb`, UNTRAINED)** | `src/world_model_vnext/`, `tests/test_p43_stage34_hctt_pipeline.py` | Implemented HCTT-256 (5,440,353 params, RoPE, causal mask), temporal dataset, `AuditTargetLoader`, 70/10/10/10 split manifest, locked losses, read-only evaluator, and barrier runner. |
| **Stage 3.5 Scientific Gate Closure** | **COMPLETED (UNTRAINED)** | `experiments/stage35_production/`, `tests/test_p44_stage35_gate_closure.py` | Closed all Stage 3.4 gaps: generated `500` Prelift $T=40$ (`6,000` strict `H=20` windows) + `120` sealed `final_holdout`; separated Path 1 strict vs Path 2 masked windows; added unactivated 3-term objective config; benchmarked matched $\Delta t=0.01\text{ s}$ Persistence/Const-Vel & native-$\Delta t=0.02\text{ s}$ Peg B1–B3; quarantined Stage 3.4 `test`; enforced runtime CSPRNG HMAC salt (`63/63` regression tests passed). |
| **Stage 4 Student Model Training** | **LOCKED (`0` STEPS)** | `src/world_model_vnext/runner.py` | `training_permitted = 0`, `execution_authorized = 0`, `gradient_steps = 0`, `optimizer_updates = 0`; awaiting explicit authorization for HOROS Stage 4 — First Authorized HCTT-256 Training Experiment. |

---

### 2. Checkpoint & Artifact Ledger

#### Frozen Historical Artifacts (Preserved Immutable)
- `checkpoints/phase3_4/`: 37 files (B1, B2, B3, B4 checkpoints and calibration tables).
- `experiments/stage3_preflight/pilot/accepted/`: 40 certified pilot trajectories.
- `experiments/stage3_production_readiness/stage3_3_seed_registry.json`: 1,000 production seeds + 300 reserve seeds.

#### Newly Produced Stage 3 Artifacts
- `experiments/stage3_production/training/`: 1,000 opaque HMAC `.npz` training archives (states, actions, next_states, wrenches, next_wrenches, sample_id, task).
- `experiments/stage3_production/audit/`: 1,000 `.privileged.json` audit archives and master `stage3_3_audit_manifest.json`.
- `experiments/stage3_production/accepted_manifest.json`: Complete ledger of all 1,000 accepted scenarios with SHA-256 provenance.
- `experiments/stage3_production/rejected_manifest.json`: Record of 0 rejected scenarios.
- `experiments/stage3_production/stage3_production_summary.json`: Execution metrics and mode distribution.
- `scripts/run_stage3_production_dataset.py`: Master generation and validation pipeline.
- `scripts/p5_t4r4_temporal_adapter.py`: Deterministic replay adapter for P5-T4R4.
- `reports/`: Complete suite of 10 Stage 3 technical and audit reports.

#### Newly Produced Stage 3.4 Artifacts
- `src/world_model_vnext/`: Complete HCTT-256 architecture, temporal sliding-window dataset, HMAC-joined `AuditTargetLoader`, split generator, locked multi-objective losses, read-only evaluator, and authorization-locked runner.
- `tests/test_p43_stage34_hctt_pipeline.py`: Comprehensive Stage 3.4 security, causal masking, split, loss, and barrier test suite.
- `scripts/run_stage34_pretraining_verification.py`: Pre-training verification and frozen-baseline evaluation runner.
- `experiments/stage3_production/stage34_episode_split_manifest.json` & `stage34_pretraining_eval_metrics.json`.
- `experiments/stage3_production/reports/stage34_*.md`: Stage 3.4 gap audit, architecture, dataset contract, target separation, window feasibility, and pre-training readiness reports.

#### Newly Produced Stage 3.5 Artifacts
- `experiments/stage35_production/prelift_long/`: `500` Compliant Prelift ($T=40$, $\Delta t=0.01\text{ s}$) `.npz` + `.privileged.json` archives (`100%` accepted, runtime CSPRNG HMAC).
- `experiments/stage35_production/final_holdout/`: `120` blind `final_test` (`60` Prelift $T=40$ + `60` Peg-in-Hole $T=30$) `.npz` + `.privileged.json` archives (sealed, untouched during verification).
- `experiments/stage35_production/stage35_dataset_manifest.json`, `stage35_final_split_manifest.json` (`698/126/126/120/100/450` partition ledger), and `stage35_verification_metrics.json`.
- `experiments/stage35_production/reports/stage35_*.md`: Complete Stage 3.5 window audit, dataset generation, objective resolution, matched-timestep benchmark, evaluation integrity, security/provenance audit, regression test, and pre-training readiness reports.
- `scripts/run_stage35_pipeline.py` & `tests/test_p44_stage35_gate_closure.py`: Deterministic pipeline runner and 9-test Stage 3.5 gate-closure regression suite.

---

### 3. Open Decision & Next Immediate Action

All Stage 3.5 technical, scientific, security, and evaluation-integrity gates are closed and verified (`63/63` tests passed). The system stands in a fail-closed, untrained holding state:

- **Pending Decision:** Formal authorization from the USER to initiate **HOROS Stage 4 — First Authorized HCTT-256 Training Experiment**.
- **Prohibition in Effect:** STRICTLY NO student model training, parameter optimization, or gradient backpropagation shall occur prior to receiving this explicit command.
