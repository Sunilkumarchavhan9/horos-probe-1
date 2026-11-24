# HOROS PROBE-1 — Phase 5 Task 4R4 (P5-T4R4)
## Repository Integrity & Cryptographic Checksum Report

**Project:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1  
**Milestone:** Phase 5 Corrective Research (P5-T4R4: Adaptive Preload Policy Validation)  
**Experiment Subdirectory:** `research/PROBE-1/Research-002/experiments/adaptive_preload_validation_p5_t4r4`  
**Execution Timestamp:** 2026-11-24T22:50:00+05:30  
**Status:** COMPLETE — CRYPTOGRAPHIC INTEGRITY CERTIFIED  

---

### 1. Verification Objective

This report certifies the cryptographic provenance, reproducible state, and complete byte integrity of all code, protocols, baseline definitions, experiment harnesses, evaluation data, and scientific audit records comprising milestone **P5-T4R4** (`adaptive_preload_validation_p5_t4r4`).

Every file listed below was generated, validated, and verified under strict simulation isolation in compliance with the immutable historical baseline constraints.

---

### 2. SHA-256 File Manifest

| File Name | Size (Bytes) | SHA-256 Checksum |
| :--- | :--- | :--- |
| `_paths.py` | 744 | `285822ACBF68F79573B8DD7C091618B680A57592989421CD737C0717FD6CD172` |
| `adaptive_preload_planner.py` | 6,665 | `5E0D733F5C896CDE7428079BBCCF409E6B09FEDAED91E39D296BD343BCFE0621` |
| `evidence_reconciliation.json` | 3,712 | `C371C6ADB6F25EFB489FDE4956639AF8B1E14559A96A431EF377A196D0496E0D` |
| `friction_probe_estimator.py` | 5,828 | `0AEE0616F8534FC55E3FC7AF2FA54A35B13E6C3DEBB8CEFD53D7140383071889` |
| `friction_uncertainty_model.py` | 3,402 | `6C3A79B7E3CD61ED749256D9945CC59C80656CE6112CC5156F1B9BC84B3777ED` |
| `p4_t4b_evidence_audit_addendum.md` | 4,900 | `E64DFC6DD04CB53721C911780E77F521EDD1EDBB808B32C13D32BB949AA800F2` |
| `p5_t4r2_evidence_audit_addendum.md` | 5,857 | `4BB09820E087BBD177B79A9004DB2A1E9F8B31B0081CC20BB5F5124B27722973` |
| `preload_controller_baselines.py` | 8,013 | `5D03762E0380BBCB26B52618B778CBD3219C87E01FA369BE260AC02DCFD9EB6B` |
| `preload_estimation_results.json` | 303 | `B9538CB7FDE3D91234746D5D394FA15E3BF268CB4984B344963316921CD6A2EC` |
| `preload_evidence_audit.md` | 3,721 | `E9E81E452D348138F5AD56C58CC3DCCD48A97EC5E87F55C5D2716D6704B30FE0` |
| `preload_experiment_protocol.py` | 1,675 | `CCF03986DC51EAA725555621FF6A58D0F81EEF6189DBB4B4E01FDD470711E980` |
| `preload_failure_analysis.json` | 1,392 | `FF200ABB40B3A3A23F7A302E7233C145C8529B0BEFEEE79D249A90F71F3AC8C2` |
| `preload_force_allocator.py` | 5,404 | `EF61134C9848161C4F0E05E6D1B85E1F5DB6ABC159268B6ED5D4115DF726A8C8` |
| `preload_raw_records.json` | 11,008,416 | `EF19A5B1165F90353EC6921E26C633CD52628969AE444D02EF823B7524F41734` |
| `preload_reproducibility_manifest.json` | 459 | `86199B9643969CE2D02F4F7FBD2E05C58E0C5BA17969C2B625672DB4B61A8379` |
| `preload_research_cross_milestone_analysis.md` | 5,201 | `BE29DAD325CB758BDBBA4EBBA9408F454AA15598F8C29B244929FC94731198BA` |
| `preload_scientific_evaluation_report.md` | 13,850 | `7DBB446584875F1F5A7F5A5128E7C8DD69F7873FC7F9187CA2D8F94D4C32455A` |
| `preload_statistical_analysis.json` | 2,290 | `756747A538AFEC6D4A145D5E76C00A9CC98F05AFD7CA7F61ECC7508459D1047F` |
| `preload_sweep_experiment.py` | 13,488 | `691D3451E982813243DF0F8AC7C958065E138907366407885F5F1F7955259379` |
| `preload_sweep_results.json` | 8,924 | `BA5B90DC2B577FF80C181E492E637360CD70746BE10A8A3D1126FA41491F0B02` |
| `preload_sweep_specification.md` | 3,393 | `86A28CD5EF15879A29D238A9D2091C2CE25E2E6CCFDDE33A898378E86448A22D` |
| `run_p5_t4r4_experiments.py` | 10,423 | `C3F439EEFBC2747CD71318CD80701178CFE0D9B98DF16C2BB84B7D4377E75832` |
| `test_preload_safety_and_causality.py` | 3,412 | `7ED4FEC5967CB1262A34BEBFEC16476F12DE21A1958DABFCCD2048F40B7E1419` |

---

### 3. Execution & Physical Compliance Audit

- **Total benchmark episodes executed:** 12,000 (600 held-out evaluation scenarios across 10 controllers and 2 protocols).
- **Controlled initial preload sweep episodes:** 2,100 (300 training scenarios across 7 normal force targets).
- **Friction estimation validation episodes:** 300 (300 validation scenarios).
- **Slew Rate Violations ($|\dot{F}_n| > 50\,\text{N/s}$):** **0** (strictly zero across all 14,400 simulation runs).
- **Force Bound Violations ($F_n \notin [3.0, 25.0]\,\text{N}$):** **0** (strictly zero).
- **Protocol B Budget Violations ($\sum F_n > N_{\text{active}} \times 8.0\,\text{N}$):** **0** (strictly zero).
- **Causal Isolation:** Fully verified — zero oracle parameter access; friction estimation operates exclusively on noisy sensor telemetry.
- **Unit Test Suite:** 100% pass rate (`4/4 passed` in `test_preload_safety_and_causality.py`).
