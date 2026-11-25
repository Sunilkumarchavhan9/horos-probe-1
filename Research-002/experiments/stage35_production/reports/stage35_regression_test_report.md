# HOROS PROBE-1 — Research-002
## Stage 3.5 Deliverable 09: Regression Test & Gate Closure Verification Report

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.5 — Scientific Gate Closure & HCTT-256 Training Readiness  
**Document Identifier:** `HOROS-P1-R002-STAGE35-REP-09-REGRESSION-TESTS`  
**Date:** 2026-11-25  

---

### 1. Authoritative Regression Command & Summary Result

- **Command Executed (`c:\projects\MemoryOS`):**
  ```powershell
  python -m pytest research\PROBE-1\Research-002\tests\test_p40_stage3_production_readiness.py research\PROBE-1\Research-002\tests\test_p41_stage3_production_contract.py research\PROBE-1\Research-002\tests\test_p42_stage3_3a2_production_gate.py research\PROBE-1\Research-002\tests\test_p43_stage34_hctt_pipeline.py research\PROBE-1\Research-002\tests\test_p44_stage35_gate_closure.py -v
  ```
- **Exact Result:** **`63 passed in 27.05s`** (`0 failed`, `0 skipped`, `0 errors`).

---

### 2. Per-Suite Test Inventory

| Test Module | Scope | Test Count | Verdict |
| :--- | :--- | :---: | :---: |
| `tests/test_p40_stage3_production_readiness.py` | Stage 3.3 seed registry, distributions, ontology, leakage firewall, zero-training invariants, pilot integrity | 10 | **10 / 10 PASSED** |
| `tests/test_p41_stage3_production_contract.py` | Production seed formula reconciliation, reserve pool, dual IDs, impact & wedging contracts, deterministic replay | 8 | **8 / 8 PASSED** |
| `tests/test_p42_stage3_3a2_production_gate.py` | Event-aligned rim impact, geometric jamming lock, package & tensor anti-leakage, reserve formula, provenance | 27 | **27 / 27 PASSED** |
| `tests/test_p43_stage34_hctt_pipeline.py` | HCTT-256 architecture lock, causal mask, Stage 3.4 split disjointness, HMAC corruption handling, barrier & checkpoint roundtrip | 9 | **9 / 9 PASSED** |
| `tests/test_p44_stage35_gate_closure.py` | Stage 3.5 strict 10-step history, `H in {1,3,5,10,20}` horizons, Path 1 vs Path 2 separation, Prelift `T=40` determinism, 6-partition `final_test` isolation, protected-seed exclusion, timestep & 3-term objective gating, runtime CSPRNG HMAC provenance, frozen `B1–B4` & Stage 3.3 immutability, authorization lock | 9 | **9 / 9 PASSED** |
| **Total** | | **63** | **63 / 63 PASSED** |

---

### 3. Stage 3.5 Gate Closure Traceability (`test_p44_stage35_gate_closure.py`)

1. `test_strict_10_step_history_and_horizon_availability` — Verifies `500` `prelift_long` ($T=40$) episodes produce `15,500` strict `min_future_steps=1` anchors (`valid_hist_len == 10`), exact per-horizon strict batch counts (`15,500 / 14,500 / 13,500 / 11,000 / 6,000`), full `hist_mask` and `future_mask[:, :H]`, and fails closed for out-of-range horizon requests.
2. `test_masked_vs_strict_separation_and_episode_length_feasibility` — Verifies Stage 3.3 Prelift ($T=20$) yields `0` strict `(10, 20)` windows whereas Stage 3.5 Prelift ($T=40$) yields `6,000` (`12/ep`), keeps partial-history Path 2 (`valid_hist_len` `1..9`) separated from strict Path 1 batches, and rejects invalid `window_mode`.
3. `test_prelift_t40_deterministic_reproducibility_all_strata` — Verifies bitwise replay hash equality for `compliant_prelift` ($T=40, \Delta t = 0.01\text{ s}$) across `nominal`, `probe`, and `nonstationary` strata via `verify_deterministic_reproducibility`.
4. `test_final_split_manifest_isolation_and_quarantine` — Verifies `stage35_final_split_manifest.json` (`1,620` total episodes, `verify_split_manifest_disjointness == True`), exact partition sizes (`698 / 126 / 126 / 120 / 100 / 450`), zero pairwise `sample_id` or `seed` intersection across all 6 splits, `final_test_status == SEALED_UNTIL_AUTHORIZED_STAGE4_EVAL`, and exact quarantine of all 100 Stage 3.4 `test` episodes.
5. `test_stage35_protected_seed_exclusion` — Verifies all `620` Stage 3.5 seeds (`prelift_long` + `final_holdout`) have zero overlap with Phase 3 pools, `9200..9359`, pilot seeds, `300` reserve seeds (`reserve_seed_pool`), Stage 3.3 `1,000` seeds, and `P5-T4R4` held-out seeds (`4950001..4950600`).
6. `test_timestep_contract_and_experimental_3term_objective_gating` — Verifies `PegInHoleEnv.dt == 0.02` vs `PEG_CONTROL_DT_S == 0.01`, `EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG.weight_distill == 0.0`, `objective_authorized_for_training is False`, zero contribution of any injected teacher tensor to `loss_total`, and `Persistence` / `Constant-Velocity` native $\Delta t=0.01\text{ s}$ baseline evaluation.
7. `test_privileged_isolation_and_live_csprng_hmac_provenance` — Verifies zero hardcoded audit salt hex literals in `scripts/run_stage35_pipeline.py`, live `os.urandom(32)` HMAC generation/verification + fail-closed mismatch rejection, distinct `audit_salt_sha256` from Stage 3.3, and `assert_no_privileged_keys_in_inputs`.
8. `test_frozen_phase34_checkpoints_and_stage33_archives_immutable` — Verifies `37` files intact in `checkpoints/phase3_4/`, `phase34_training_report.json` SHA-256 unchanged, `load_hybrid_checkpoint` load check across `B1–B3` + `B4` calibration JSON parse across all tasks/regimes, `1,000` Stage 3.3 `.npz` files intact, and exact SHA-256 preservation of `accepted_manifest.json` and `stage34_episode_split_manifest.json`.
9. `test_training_authorization_lock_and_verification_state` — Verifies `TRAINING_PERMITTED == 0`, `EXECUTION_AUTHORIZED == 0`, `TrainingAuthorizationError` across all runner training APIs, zero updates executed, and `final_holdout_evaluated is False`.
