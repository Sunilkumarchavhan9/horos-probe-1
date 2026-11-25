# HOROS PROBE-1 — Research-002
## Stage 3 Deliverable 10: Research Register & Program State Update

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.6 — Blind Holdout Quarantine & Evaluation Integrity Recovery  
**Document Identifier:** `HOROS-P1-R002-STAGE3-REP-10-RESEARCH-REGISTER`  
**Date:** 2026-11-25  
**Current Milestone:** **Stage 3.6 Complete (Compromised Holdout Quarantined; Fail-Closed Evaluation Guards Active; HCTT-256 Training-Ready, Untrained)**  
**Next Milestone:** **HOROS Stage 4 — First Authorized HCTT-256 Training Experiment & Private Holdout Generation** *(Locked)*  
**Governance Status:** **TRAINING LOCKED (`training_permitted = 0`, `execution_authorized = 0`, `gradient_steps = 0`, `optimizer_updates = 0`, `final_holdout_evaluation = BLOCKED`) — AWAITING FORMAL USER AUTHORIZATION**

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
| **Stage 3.5 Scientific Gate Closure** | **ENGINEERING COMPLETE (HOLDOUT COMPROMISED)** | `experiments/stage35_production/`, `tests/test_p44_stage35_gate_closure.py` | Generated `500` Prelift $T=40$ (`6,000` strict `H=20` windows); matched $\Delta t=0.01\text{ s}$ baselines; `63/63` regression tests historically passed. Final holdout (120 ep) committed to public GitHub in `6f0cae6`; **permanently compromised as a blind evaluation benchmark**. |
| **Stage 3.6 Holdout Quarantine** | **COMPLETE (VERIFIED)** | `experiments/stage36_holdout_quarantine/`, `tests/test_p45_stage36_holdout_quarantine.py` | Formally quarantined `stage35_final_holdout` under status `COMPROMISED_PUBLIC_EXPOSURE`; implemented fail-closed code guards in 7 core modules; authored 13/13 passing security tests (`test_p45`); locked final evaluation until private holdout generation; preserved all train/val/calib splits intact. |
| **Stage 3.6A Quarantine Hardening** | **CERTIFIED (92/92 PASS)** | `src/world_model_vnext/`, `tests/test_p46_stage36a_quarantine_hardening.py` | Hardened quarantine against renamed/relocated archives with streaming SHA-256 pre-deserialization checks; constructor fail-closed on compromised roots; hardened split and credential verifications; 16/16 adversarial tests passed (total 92/92 regression suite). |
| **Stage 3.6B Fail-Closed Correction** | **ACCEPTED (102/102 PASS)** | `scripts/stage3_production_adapter.py`, `tests/test_p47_stage36b_fail_closed_quarantine.py` | Eliminated fail-open import in production dataloader; mandatory verifier import and registry checks fail closed with `SecurityViolationError`; `np.load()` never called on rejected archives; 10/10 security tests passed (total 102/102 regression suite). |
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
- `experiments/stage35_production/final_holdout/`: `120` blind `final_test` (`60` Prelift $T=40$ + `60` Peg-in-Hole $T=30$) `.npz` + `.privileged.json` archives (**QUARANTINED AS COMPROMISED BENCHMARK**).
- `experiments/stage35_production/stage35_dataset_manifest.json`, `stage35_final_split_manifest.json` (`698/126/126/120/100/450` partition ledger), and `stage35_verification_metrics.json`.
- `experiments/stage35_production/reports/stage35_*.md`: Complete Stage 3.5 window audit, dataset generation, objective resolution, matched-timestep benchmark, evaluation integrity, security/provenance audit, regression test, and pre-training readiness reports.
- `scripts/run_stage35_pipeline.py` & `tests/test_p44_stage35_gate_closure.py`: Deterministic pipeline runner and 9-test Stage 3.5 gate-closure regression suite.

#### Newly Produced Stage 3.6 Artifacts
- `experiments/stage36_holdout_quarantine/holdout_quarantine_record.json`: Authoritative quarantine record registering `stage35_final_holdout` as `COMPROMISED_PUBLIC_EXPOSURE` with all eligibility flags set to `false`, populated with 120 `.npz` and 120 `.privileged.json` cryptographic SHA-256 digests.
- `experiments/stage36_holdout_quarantine/stage36_data_flow_exposure_audit.md`: Comprehensive audit of all data-flow routes and references.
- `experiments/stage36_holdout_quarantine/stage36_holdout_quarantine_report.md`: Technical and scientific quarantine report (including Stage 3.6A hardening).
- `experiments/stage36_holdout_quarantine/stage36_stage4_readiness_assessment.md`: Architectural distinction matrix and Stage 4 pre-requisites.
- `experiments/stage36_holdout_quarantine/stage36_historical_integrity_report.md`: Verification audit of preserved immutable checkpoints and partitions.
- `tests/test_p45_stage36_holdout_quarantine.py`: 13-test regression and fail-closed security suite covering all quarantine invariants.
- Fail-closed code guards in `src/world_model_vnext/` (`contracts.py`, `dataset.py`, `target_loader.py`, `evaluator.py`, `splits.py`, `runner.py`, `__init__.py`) and `scripts/stage3_production_adapter.py`.

#### Newly Produced Stage 3.6A Artifacts
- `tests/test_p46_stage36a_quarantine_hardening.py`: 16-test adversarial penetration and hardening test suite.
- Hardened payload defenses in `src/world_model_vnext/contracts.py` (streaming SHA-256 content checking before file deserialization).
- Pre-load rejection in `src/world_model_vnext/dataset.py` (checks file hash before `np.load`) and `target_loader.py` (pre-parse hash check on privileged JSON).
- Fail-closed constructor enforcement in `src/world_model_vnext/target_loader.py` (`AuditTargetLoader` raises `HoldoutIneligibleError` during construction on compromised roots).
- Manifest and credential hardening in `src/world_model_vnext/splits.py` (positive presence of sample IDs and seeds required) and `evaluator.py` (caller-supplied boolean credentials rejected).

#### Newly Produced Stage 3.6B Artifacts
- `tests/test_p47_stage36b_fail_closed_quarantine.py`: 10-test fail-closed quarantine and security exception verification suite.
- Fail-closed archive loading in `scripts/stage3_production_adapter.py`: eliminated `ImportError` bypass, enforced mandatory verifier import, and wrapped all verification errors in `SecurityViolationError` before `np.load()`.
- Fail-closed quarantine registry loading in `src/world_model_vnext/contracts.py`: `_load_quarantine_record_data`, `get_compromised_sample_ids`, `get_compromised_npz_sha256`, and `get_compromised_privileged_sha256` strictly raise `ContractViolationError` on missing or malformed registries (< 120 items).

---

### 3. Current Scientific Governance Status

The official system status is explicitly partitioned across four dimensions:

```text
STAGE_3_5_IMPLEMENTATION = COMPLETE
STAGE_3_5_REGRESSION = HISTORICALLY_PASSED
STAGE_3_5_HOLDOUT_INTEGRITY = COMPROMISED
STAGE_3_6_QUARANTINE = COMPLETE_ONLY_AFTER_TESTED
NEW_PRIVATE_HOLDOUT = NOT_CREATED
BLIND_FINAL_EVALUATION = BLOCKED
STAGE_4_TRAINING = NOT_AUTHORIZED
```

- **Pending Decision:** Formal authorization from the USER to initiate **HOROS Stage 4 — First Authorized HCTT-256 Training Experiment**.
- **Prohibition in Effect:** STRICTLY NO student model training, parameter optimization, or gradient backpropagation shall occur prior to receiving this explicit command.
- **Holdout Evaluation Gate:** Final scientific validation is BLOCKED until an independently generated, private holdout dataset is created under a separate authorized protocol outside public repositories.
