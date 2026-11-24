# HOROS PROBE-1 — Research-002
## Stage 3.5 Deliverable 04: Training Objective & Distillation Resolution

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.5 — Scientific Gate Closure & HCTT-256 Training Readiness  
**Document Identifier:** `HOROS-P1-R002-STAGE35-REP-04-OBJECTIVE-RESOLUTION`  
**Date:** 2026-11-25  

---

### 1. Problem Statement

The locked multi-objective specification (`HOROS-P1-R002-SPEC-LOCKED-001`, encoded in `LOCKED_HCTT_CONFIG` inside `src/world_model_vnext/contracts.py`) specifies four terms:
$$\mathcal{L}_{\text{locked}} = 1.0\,\mathcal{L}_{\text{sim\_dynamics}} + 0.5\,\mathcal{L}_{\text{mode\_focal}} + 0.25\,\mathcal{L}_{\text{sys\_id}} + 0.10\,\mathcal{L}_{\text{distill}}.$$
However, every certified teacher in `src/teaching/` (`Stage3ProductionTeacherController`, `DeterministicMockTeacher`) has `has_representations = False`: `AuditTargetLoader.get_batch_targets()` yields `teacher_representations = None` and `distillation_target_available = False`. Consequently, during read-only checks on Stage 3 data, `L_distill = 0.0` and `distillation_active = False`. Training on the 4-term specification while silently dropping `L_distill` risks misrepresenting the active training objective.

---

### 2. Dual-Configuration Architecture Resolution

Rather than silently mutating `LOCKED_HCTT_CONFIG` or fabricating dummy teacher embeddings:
1. **Locked 4-Term Specification (`LOCKED_HCTT_CONFIG`) Preserved Intact:** Keeps `weight_distill = 0.10`, `objective_profile = "locked_4term_with_gated_distill"`, and `objective_authorized_for_training = False` (or requires genuine teacher hidden states to activate `L_distill`).
2. **Unactivated 3-Term Teacher-Free Experimental Configuration (`EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG`):** Added cleanly in `src/world_model_vnext/contracts.py`:
   - `objective_profile = "experimental_teacher_free_3term_no_distill"`
   - `objective_authorized_for_training = False` (explicitly unactivated until formal Stage 4 governance decision)
   - Weights: `weight_sim_dynamics = 1.0`, `weight_mode_focal = 0.5`, `weight_sys_id = 0.25`, `weight_distill = 0.0`
   - Verified in `HCTTMultiObjectiveLoss` (`src/world_model_vnext/losses.py`) and regression-tested to ignore any injected teacher tensor (`0.0 * l_distill`) so only the 3 grounded terms enter `loss_total`.

#### Verification Values (`torch.no_grad()` on `val` Batch, `CompliantPreLift`)
Verified identically before and after parameter norm check (`parameters_unchanged = True`, `training_steps_executed = 0`):

| Metric Component | Locked 4-Term (`teacher_reps=None`) | Experimental 3-Term (`weight_distill=0.0`) |
| :--- | :---: | :---: |
| `loss_total` | `11.64045238494873` | `11.64045238494873` |
| `l_sim_dynamics` | `10.996219635009766` | `10.996219635009766` |
| `l_state_mse` | `5.41998291015625` | `5.41998291015625` |
| `l_wrench_mse` | `3.238295316696167` | `3.238295316696167` |
| `l_mode_focal` | `1.1656882762908936` | `1.1656882762908936` |
| `l_sys_id` | `0.24555537104606628` | `0.24555537104606628` |
| `l_distill` | `0.0` | `0.0` |
| `distillation_active` | `False` | `False` |
| `objective_authorized_for_training` | `False` (via `TRAINING_PERMITTED=0`) | `False` (unactivated config + `TRAINING_PERMITTED=0`) |

---

### 3. Architectural Trade-Off Analysis & Explicit Recommendation

| Criterion | Option A: Approve 3-Term Teacher-Free Stage 4 Baseline | Option B: Integrate Representation-Producing Teacher First |
| :--- | :--- | :--- |
| **Physical Grounding** | Supervised purely by simulator ground-truth trajectories ($s, w$), causal operational contact modes ($0..4$), and latent $(\mu, m)$ sys-ID targets. Zero synthetic bias. | Adds external inductive bias ($d_{\text{model}}=256$ cosine alignment), which helps only if the teacher encodes superior contact-mode geometry at $\Delta t = 0.01\text{ s}$. |
| **Readiness & Risk** | **Ready immediately** once Stage 4 authorization is granted; removes external teacher dependency and avoids cross-task $\Delta t$ or representation-space projection mismatch. | Currently **unavailable** (`has_representations = False` across existing teachers); requires designing/validating a projection adapter and proving non-degradation vs pure physics supervision. |
| **Interpretability** | Clean ablation baseline: establishes how well pure simulator physics + mode focal + sys-ID trains HCTT-256 before adding distillation. | Confounds representational distillation effects with sequence-modeling improvements during the first HCTT-256 training run. |

#### Explicit Recommendation
**Proceed in Stage 4 with Option A — authorize `EXPERIMENTAL_3TERM_OBJECTIVE_CONFIG` (`1.0*L_sim + 0.5*L_mode + 0.25*L_sysid`, `weight_distill=0.0`) as the primary Stage 4 Run-1 baseline objective**, while keeping `LOCKED_HCTT_CONFIG` ready as a future **Stage 4B distillation ablation** only if a representation-producing physical teacher is integrated and validated. Both configurations remain locked (`TRAINING_PERMITTED = 0`, `EXECUTION_AUTHORIZED = 0`) pending explicit user authorization.
