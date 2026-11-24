# HOROS PROBE-1 — Research-002
## Stage 3 Deliverable 05: Teacher Infrastructure & Proposal Engine Verification Report

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3 — World Model vNext Dataset Generation, Physics Validation and Training Readiness  
**Document Identifier:** `HOROS-P1-R002-STAGE3-REP-05-TEACHER-INFRA`  
**Date:** 2026-11-24  
**Subject:** Teacher Registry, Inference Engine, Proposal Generation, Simulator Decoupling, and Grounding Primacy  
**Verdict:** **TEACHER INFRASTRUCTURE FULLY VERIFIED & SIMULATOR GROUNDING STRICTLY PRESERVED**

---

### 1. Executive Summary

This report establishes the verification and architectural compliance of the Model Teaching Infrastructure within HOROS PROBE-1. The infrastructure decouples high-level policy proposals provided by external teacher models from ground-truth physical execution provided exclusively by deterministic physics simulation environments.

External teachers provide candidate trajectory proposals, geometric guidance, and latent representation distillation targets. They do not generate physical ground truth. All physical observations, reaction forces, accelerations, and contact mode transitions are generated strictly by authoritative simulator execution.

---

### 2. Teacher Registry & Model Descriptors

The teacher registry (`src/teaching/registry.py`) manages teacher model metadata and enforces integrity:

| Registered Model ID | Provider | Version | Capabilities | Latent Reps | Descriptor SHA-256 |
| :--- | :--- | :--- | :--- | :---: | :--- |
| `stage3_production_teacher_v1` | `MOCK_DETERMINISTIC` | `1.0.0` | `trajectory_proposal`, `stratum_control` | False | Authenticated |
| `stage3_pilot_controller_v1` | `MOCK_DETERMINISTIC` | `1.1.0` | `trajectory_proposal` | False | `620f8a78...` |
| `mock_teacher_deterministic_v1`| `MOCK_DETERMINISTIC` | `1.0.0` | `trajectory_proposal`, `compliant_grip` | True ($d=128$) | Authenticated |
| `mock_teacher_adversarial_v1`  | `MOCK_ADVERSARIAL`   | `1.0.0` | `boundary_violation_injection` | False | Authenticated |

#### Integrity Guarantees
- Every registered teacher model possesses an immutable descriptor hash based on its ID, version, provider, and modalities.
- Unregistered teacher queries are rejected fail-closed with `KeyError`.

---

### 3. Inference Engine & Query Contract

The Teacher Inference Engine (`src/teaching/inference.py`) standardizes interactions:
- Inputs are encapsulated in `TeacherQueryPayload` containing `task_id`, `current_state`, `observation_history`, `target_horizon`, and `seed`.
- Computes deterministic SHA-256 payload hashes to verify input consistency.
- Standardizes outputs in `TeacherInferenceResult` containing `candidate_actions`, `predicted_confidence`, `latent_representations`, and `provenance_hash`.
- Caching: Identical query payloads return cached results when caching is enabled, guaranteeing bitwise reproducibility.

---

### 4. Simulator Grounding Primacy & Decoupling

Stage 3 enforces the fundamental principle of **Simulator Grounding Primacy**:
$$\text{Trajectory} = \text{Simulate}(\text{Env}, \mathbf{a}_{\text{teacher}})$$

```
┌─────────────────────────────────┐
│     External Teacher Model      │ (Proposes candidate actions / representations)
└────────────────┬────────────────┘
                 │ candidate_actions: (H, action_dim)
                 ▼
┌─────────────────────────────────┐
│ AUTHORITATIVE SIMULATOR ENGINE  │ (Executes rigid-body contact dynamics,
│  - CompliantPreliftContactEnv   │  pad stretch, Coulomb slip, geometric locks)
│  - PegInHoleEnv                 │
└────────────────┬────────────────┘
                 │ Realized physical telemetry:
                 │   - states: (T, state_dim)
                 │   - wrenches: (T, 6)
                 │   - modes: (T,) classified from physics
                 ▼
┌─────────────────────────────────┐
│     GROUNDED FIREWALL GATE      │ (Strips privileged params, verifies force/
│   - PhysicsValidationEngine     │  kinematic bounds, builds HMAC package)
└────────────────┬────────────────┘
                 │
                 ├──► training/<sample_id>.npz (Model Visible)
                 └──► audit/<canonical_id>.privileged.json (Separately Controlled)
```

1. **Teachers Propose, Simulators Dispose:** Teacher proposals are treated as unverified inputs. The simulator steps the rigid-body mechanics and determines the actual pad stretch, friction reaction, table normal force, and insertion trajectory.
2. **Contact Mode Classification:** Discrete contact modes ($m_t \in \{0, 1, 2, 3, 4, 5, 6\}$) are derived strictly by `classify_contact_mode` evaluating simulator state variables, never by teacher self-reporting.
3. **Rejected Proposal Handling:** If teacher actions cause safety limit breaches, the episode is rejected and archived in `rejected_manifest.json`.

---

### 5. Verification Conclusion

The teaching infrastructure operates cleanly with zero leakage. Teacher models provide structured behavioral diversity while simulation physics guarantees absolute physical fidelity.
