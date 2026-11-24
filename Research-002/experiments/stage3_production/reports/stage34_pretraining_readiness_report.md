# HOROS PROBE-1 — Research-002
## Stage 3.4 Deliverable 13: Pre-Training Readiness & Verification Report

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.4 — HCTT-256 Implementation and Temporal Training-Pipeline Completion  
**Governance Lock State:** **`training_permitted = 0` | `execution_authorized = 0` | `training_steps_executed = 0` | `parameter_updates_executed = 0`**  
**Date:** 2026-11-25  

---

### 1. Explicit Governance Declaration

**ZERO student-model training has occurred.** Zero backward passes (`loss.backward()`), zero optimizer steps, zero learning-rate iterations, and zero weight updates were executed in Stage 3.4. `HCTTWorldModel` parameters remain at deterministic initialization (`seed=3400`), verified unchanged before and after read-only `torch.no_grad()` checks.

---

### 2. Stage 3.4 Implementation & Verification Summary

| Deliverable / Component | Path | Status | Verification Evidence |
| :--- | :--- | :---: | :--- |
| **HCTT-256 Architecture** | `src/world_model_vnext/hctt.py`, `contracts.py` | **IMPLEMENTED** | 6 layers, $d_{\text{model}}=256$, 8 heads, $d_{\text{head}}=32$, RoPE, 5,440,353 params; causal mask verified |
| **Temporal Dataset Pipeline** | `src/world_model_vnext/dataset.py` | **IMPLEMENTED** | Ingests all 1,000 NPZs unmodified; causal past-action alignment; homogeneous task batching |
| **Separate Audit Target Loader** | `src/world_model_vnext/target_loader.py` | **IMPLEMENTED** | Verified all 1,000 `.privileged.json` files via HMAC + manifest bijection; fail-closed |
| **Episode Split Manifest** | `experiments/stage3_production/stage34_episode_split_manifest.json` | **GENERATED** | 700 Train / 100 Val / 100 Calib / 100 Test; 0 overlaps across splits & protected/reserve pools |
| **Locked Multi-Objective Loss** | `src/world_model_vnext/losses.py` | **IMPLEMENTED** | `1.0*L_sim + 0.5*L_mode + 0.25*L_sysid + 0.10*L_distill`; distillation strictly gated (`False` when absent) |
| **Read-Only Evaluator** | `src/world_model_vnext/evaluator.py` | **IMPLEMENTED** | Evaluates HCTT + frozen B1–B3 on exact Stage 3.4 test split (`no_grad`) |
| **Locked Training Runner** | `src/world_model_vnext/runner.py` | **IMPLEMENTED** | Raises `TrainingAuthorizationError` on `train`/`backward`/`step_optimizer`; checkpoint roundtrip verified |
| **Stage 3.4 Test Suite** | `tests/test_p43_stage34_hctt_pipeline.py` | **PASSED** | Unit & integration tests cover security, masking, splits, alignment, and barrier |

---

### 3. Empirical Pre-Training Baseline & Untrained Smoke Evaluation (Stage 3.4 Test Split)

Executed strictly read-only on the **100-episode Stage 3.4 `test` split** (50 Compliant Prelift = 1,000 windows; 50 Peg-in-Hole = 1,500 windows), recorded in `experiments/stage3_production/stage34_pretraining_eval_metrics.json`:

#### 3.1 Compliant Prelift (50 Test Episodes, 1,000 Windows)
| Model | Status | `H=1` MSE | `H=5` MSE | `H=10` MSE | `H=20` MSE ($n=50$) | Mode Macro-F1 (All 5) | Mode Macro-F1 (4 Present) | OOD AUROC |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **B1 (Frozen M4)** | Frozen | 0.1185 | 3.4029 | 21.1850 | 75.1473 | 0.5663 | 0.7079 | 0.5000 |
| **B2 (Frozen M4)** | Frozen | 0.1135 | 2.2303 | 14.6243 | 13.7217 | 0.5087 | 0.6359 | 0.1887 |
| **B3 (Frozen M4)** | Frozen | **0.0959** | **0.7522** | **4.3704** | **9.7741** | 0.3345 | 0.4181 | 0.6978 |
| **HCTT-256 (Untrained)** | **Untrained** | 0.1332 | 0.8550 | 2.3038 | 9.3768 | 0.1243 | 0.1554 | 0.2467 |

#### 3.2 Peg-in-Hole (50 Test Episodes, 1,500 Windows)
| Model | Status | `H=1` MSE | `H=5` MSE | `H=10` MSE | `H=20` MSE ($n=550$) | Mode Macro-F1 (All 5) | OOD AUROC |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **B1 (Frozen M4)** | Frozen | 343.9288 | 512.0065 | 2751.4301 | 15703.9605 | 0.2192 | 0.5000 |
| **B2 (Frozen M4)** | Frozen | **162.5258** | **109.0265** | **171.8159** | 1126.4585 | 0.0946 | 0.5719 |
| **B3 (Frozen M4)** | Frozen | 376.1669 | 192.8489 | 177.8471 | **1013.8381** | 0.1567 | **0.8823** |
| **HCTT-256 (Untrained)** | **Untrained** | 42.8853 | 70.4754 | 117.0785 | 175.7946 | 0.0650 | 0.2950 |

- **No-Grad Loss Verification (`HCTTTrainingRunner.verify_forward_and_loss_no_grad`):**
  - `compliant_prelift`: `loss_total = 3.0428`, `l_sim_dynamics = 2.4465`, `l_mode_focal = 0.9327`, `l_sys_id = 0.5196`, `l_distill = 0.0` (`distillation_active = False`).
  - `peg_in_hole`: `loss_total = 69.3377`, `l_sim_dynamics = 68.6570`, `l_mode_focal = 1.1407`, `l_sys_id = 0.4413`, `l_distill = 0.0` (`distillation_active = False`).

---

### 4. Unresolved Scientific Issues & Exact Steps Remaining Before Training

1. **Teacher Distillation Signal Absent (`distillation_active = False`):** Stage 3.3 trajectories were generated with deterministic mock controllers (`has_representations=False`), so `L_distill` currently contributes `0.0`. Decision required before training: either authorize training with `beta_distill=0.10` gracefully zeroed (3-term physical objective) or run an authorized representation teacher first.
2. **Prelift Episode Length vs `H=20` Windowing:** Because Stage 3.3 Prelift episodes are $T=20$, zero unpadded `(H_hist=10, H_fut=20)` windows exist (`n=50` masked `t=0` windows only). Long-horizon evaluation on Prelift requires $T \ge 35$ episodes if full 10-step history conditioning at $H=20$ is desired.
3. **Constant Mass in Peg-in-Hole (`mass_kg = 0.350`):** Mass identification $R^2$ is mathematically undefined on Peg-in-Hole and can only be benchmarked on `compliant_prelift`.
4. **Mode Vocabulary Coverage:** Stage 3.3 contains modes `{0..4}` only (`Jamming=5`, `Release=6` have 0 examples).
5. **Phase 3.4 Peg-in-Hole Baseline `dt` Shift:** Frozen Phase 3.4 Peg-in-Hole baselines (`dt=0.02s`) show large MSE (`162–376` at `H=1`) on Stage 3.3 (`dt=0.01s`) trajectories; fair baseline comparisons on Stage 3.4 splits will require re-training or retraining baselines under `dt=0.01s` in Stage 4, OR evaluating HCTT on `PHASE3_SEED_POOLS` (`3000..3119`) under matched `dt`.
6. **Legacy Committed HMAC Salt:** Should be rotated from source literal to secret env var for any future external/held-out dataset generation.
7. **P5-T4R4 Integration Deferred:** Excluded pending reconciliation of replay discrepancy (`seed=4950001`) and unified contact-count tokenization.

**Remaining Steps Prior to First Authorized Run:** User review of the 7 items above and explicit authorization lifting `TRAINING_PERMITTED = 0` / `EXECUTION_AUTHORIZED = 0`.
