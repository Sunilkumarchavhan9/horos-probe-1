# HOROS PROBE-1 — Research-002
## Stage 3.4 Deliverable 02: HCTT-256 Architecture Implementation Report

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.4 — HCTT-256 Implementation and Temporal Training-Pipeline Completion  
**Implementation Package:** `src/world_model_vnext/` (`hctt.py`, `contracts.py`, `losses.py`, `runner.py`)  
**Training Status:** **STRICTLY UNTRAINED (`training_permitted = 0`, `execution_authorized = 0`, `training_steps = 0`)**

---

### 1. Locked Architecture Specification Verification

The implemented `HCTTWorldModel` in `src/world_model_vnext/hctt.py` conforms strictly to `HOROS-P1-R002-SPEC-LOCKED-001` (`horos_vnext_locked_training_spec.md`) and `manifests/horos_world_model_vnext_manifest.json` (schema `horos_world_model_vnext_manifest_v1.1`):

| Component / Hyperparameter | Locked Specification | Implemented Value | Verification Status |
| :--- | :--- | :--- | :---: |
| **Model Family** | History-Conditioned Temporal Transformer (HCTT) | `HCTTWorldModel` | **EXACT MATCH** |
| **Decoder Layers** | 6 Decoder Layers | 6 `HCTTDecoderLayer` blocks (`nn.ModuleList`) | **EXACT MATCH** |
| **Hidden Dimension ($d_{\text{model}}$)** | 256 | 256 | **EXACT MATCH** |
| **Attention Heads** | 8 Heads | 8 Heads (`d_head = 32`) | **EXACT MATCH** |
| **Positional Encoding** | Rotary Position Embeddings (RoPE) | 1D `RotaryPositionEmbedding` ($\theta = 10000.0$) | **EXACT MATCH** |
| **Attention Masking** | Causal temporal attention | Combined lower-triangular causal + key-padding mask | **EXACT MATCH** |
| **Observation History ($H_{\text{hist}}$)** | 10 steps ($100\text{ ms}$ at $\Delta t = 0.01\text{ s}$) | `history_horizon = 10` | **EXACT MATCH** |
| **Prediction Horizons** | `1, 3, 5, 10, 20` steps | `prediction_horizons = (1, 3, 5, 10, 20)` | **EXACT MATCH** |
| **Task State / Action Interfaces** | Prelift `(23, 5, 6)`, Peg-in-Hole `(15, 6, 6)` | Separate `nn.ModuleDict` per task | **EXACT MATCH** |
| **Contact Mode Head** | 5 operational modes | 5-class logits (`Free, FirstContact, Sticking, Sliding, Impact`) | **EXACT MATCH** |

---

### 2. Parameter Breakdown (Measured on Untrained Initialization)

Exact parameter count measured via `HCTTWorldModel.parameter_summary()` (`total_parameters = 5,440,353`, `trainable_parameters = 5,440,353`, **0 updates executed**):

| Submodule | Parameter Count | Responsibility |
| :--- | :---: | :--- |
| `layers` (6 Transformer blocks) | 4,738,560 | Causal RoPE multi-head attention + Pre-LN GELU FFN ($d_{\text{ff}}=1024$) |
| `history_token_fusion` | 230,400 | Fuses task state, prev action, 6-axis wrench, 3 contact signals, 5 mode probs |
| `kinematics_delta_head` | 141,350 | Task-specific future state residual predictions ($\Delta \hat{s}_{t+k}$) |
| `kinematics_logvar_head` | 70,694 | Task-specific aleatoric state log-variance bounds |
| `wrench_out_head` & `wrench_logvar_head` | 101,004 | Shared 6-axis wrench prediction ($F_x, F_y, F_z, T_x, T_y, T_z$) + log-var |
| `latent_friction_head`, `latent_mass_head`, `epistemic_trust_head` | 99,075 | Softplus $(\hat{\mu}, \hat{m})$ SysID heads + Sigmoid trust head ($\hat{u}_t \in [0, 1]$) |
| `contact_mode_head` | 33,541 | 5-class contact mode logits (`pred_mode_logits`, `hist_mode_logits`) |
| Input projections & embeddings | 25,729 | Task embeddings, `state_in_proj`, `prev_action_in_proj`, `future_action_in_proj`, etc. |
| **Total** | **5,440,353** | |

---

### 3. Zero-Leakage Inference Guarantees & Model-Own Mode Feedback

1. **Strict Non-Privileged Inputs:** `HCTTWorldModel.forward()` accepts ONLY `task`, `hist_states`, `hist_prev_actions`, `hist_wrenches`, `hist_mask`, `future_actions`, `future_action_mask`, and optional `prev_mode_probs`. Passing any privileged key via `extra_inputs_for_audit` raises `ContractViolationError`.
2. **Observable Continuous Contact Signals (`extract_observable_contact_signals`):** Derives $[\|F_N\|, s_{\text{pad}}, v_{\text{slip}}] \in \mathbb{R}^3$ strictly from model-visible fingertip forces/velocities (Prelift) or 6-axis wrench/peg velocity (Peg-in-Hole), never accessing $\mu_{\text{true}}$ or simulator strata.
3. **Model-Own Previous Mode Feedback:** When `prev_mode_probs` is not supplied by a closed-loop caller, `forward()` runs a causal self-prediction pass over history with a uniform $1/5$ prior, shifts the resulting `softmax(contact_mode_head(hist_hidden))` right by 1 step (`[0.2, p_hat_0, ..., p_hat_{L-2}]`), and feeds ONLY the model's own prior predictions into the main pass. Ground-truth `modes` NEVER enter `forward()`.
4. **Causal Masking Invariant Verification:** Tested in `test_p43_stage34_hctt_pipeline.py` — perturbing `future_actions[:, 5:, :]` causes **0.0** change (`atol=1e-6`) to `latent_history_repr`, `pred_friction_mu`, and predicted states for steps $k < 5$.

---

### 4. Trust Score & Conformal Calibration Reconciliation

- **Manifest Alignment:** `horos_world_model_vnext_manifest.json` lists `calibrated_epistemic_trust_score` ($\hat{u}_t \in [0, 1]$) alongside conformal coverage $\ge 90\%$.
- **Explicit Pre-Calibration State:** `HCTTWorldModel` provides `epistemic_trust_head` ($\hat{u}_t$), predictive uncertainty (`pred_uncertainty_total`), and conformal buffers (`trust_calibrated = False`, `conformal_quantiles = NaN`).
- **Anti-Overclaim Guard:** `predict_with_uncertainty()` explicitly returns `trust_calibrated = False`, and `HCTTReadOnlyEvaluator` reports `conformal_coverage = "UNCALIBRATED_PRETRAINING"` until `attach_conformal_quantiles(Q)` is called after an authorized training run + held-out `calib` split conformal calibration (`MultiHorizonQuantileEngine`).
