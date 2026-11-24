# HOROS PROBE-1 — Research-002
## Stage 3.4 Deliverable 03: Temporal Dataset Contract & Sequence Window Pipeline

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.4 — HCTT-256 Implementation and Temporal Training-Pipeline Completion  
**Implementation:** `src/world_model_vnext/dataset.py` (`HCTTTemporalDataset`)  
**Dataset Root:** `experiments/stage3_production/training/` (1,000 unmodified NPZ archives)  

---

### 1. NPZ Ingestion & Schema Validation

`HCTTTemporalDataset` ingests the 1,000 Stage 3.3 `.npz` archives in read-only mode (`allow_pickle=False`) without modifying any existing file:

- **Colocated Audit Refusal:** Refuses loading if any `.privileged.json`, `audit`, `seed_registry`, or `accepted_manifest` file exists inside `training/`.
- **Exact Allowlist Check:** Requires each archive's keys to match `{"states", "actions", "next_states", "wrenches", "next_wrenches", "sample_id", "task"}` exactly.
- **Task Schema Validation:**
  - `compliant_prelift`: `T = 20`, `state_dim = 23`, `action_dim = 5`, `wrench_dim = 6`, $\Delta t = 0.01\text{ s}$.
  - `peg_in_hole`: `T = 30`, `state_dim = 15`, `action_dim = 6`, `wrench_dim = 6`, $\Delta t = 0.01\text{ s}$.
- **Temporal Continuity Invariant:** Explicitly verifies `states[1:] == next_states[:-1]` and `wrenches[1:] == next_wrenches[:-1]` (`atol=1e-5`) and finite values across every episode.

---

### 2. Causal Past-Action & History Window Alignment

To enforce strict zero future leakage at anchor step $t \in \{0, \dots, T-1\}$:

1. **Historical Observation Window (`H_hist = 10`, $100\text{ ms}$):**
   - Observation states & wrenches are drawn from $[\max(0, t-9), \dots, t]$ and right-aligned into `(10, D)` with left-padded indices strictly masked (`hist_mask[j] = False`).
   - **Past-Action Causality Rule:** Historical slot $\tau$ pairs observation $(s_\tau, w_\tau)$ strictly with the *previously executed* action `actions[tau - 1]` (or $\mathbf{0}$ when $\tau = 0$). **Action `actions[t]` is excluded from `hist_prev_actions` and placed exclusively in `future_actions[:, 0]`**, preventing future action leakage into causal history tokens.
2. **Future Candidate-Action Conditioning (`H_fut = 20`):**
   - Candidate actions `actions[t : min(T, t + 20)]` populate `future_actions[:, :n_fut]` with `future_mask[:, :n_fut] = True`.
   - Beyond-episode steps ($k \ge n_{\text{fut}}$) are right-padded with zeros and strictly masked (`future_mask = False`) in attention and loss/evaluation calculations.
3. **Model Input / Target Separation:**
   - `split_batch_inputs_and_targets(batch)` isolates `model_inputs` (`task`, `hist_states`, `hist_prev_actions`, `hist_wrenches`, `hist_mask`, `future_actions`, `future_action_mask`) from `sim_targets` (`target_next_states`, `target_next_wrenches`, `future_mask`).
   - `sample_id` and `anchor_step` remain outside model tensors strictly for audit provenance tracking.

---

### 3. Homogeneous Task Batching & P5-T4R4 Boundary

- `iter_homogeneous_batches()` groups windows strictly by `task`, producing deterministic homogeneous tensors (`(B, 10, 23)` / `(B, 20, 5)` for Prelift and `(B, 10, 15)` / `(B, 20, 6)` for Peg-in-Hole). Mixed-task collation raises `ContractViolationError`.
- **P5-T4R4 Isolation:** P5-T4R4 trajectories ($D_s \in \{18, 22, 26\}$, $D_a \in \{3, 4, 5\}$) remain separated from `HCTTTemporalDataset` due to incompatible state/action layouts and an unresolved replay discrepancy detected on `seed=4950001`.
