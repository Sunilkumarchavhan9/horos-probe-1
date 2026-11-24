# HOROS PROBE-1 — Research-002
## Stage 3 Deliverable 04: P5-T4R4 Temporal Data Integration and Replay Adapter Audit

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3 — World Model vNext Dataset Generation, Physics Validation and Training Readiness  
**Document Identifier:** `HOROS-P1-R002-STAGE3-REP-04-P5-T4R4-AUDIT`  
**Date:** 2026-11-24  
**Subject:** P5-T4R4 Raw Record Structure, Data Contract Divergence, and Replay Adapter Verification  
**Adapter Implementation:** `research/PROBE-1/Research-002/scripts/p5_t4r4_temporal_adapter.py`  
**Verdict:** **AUDIT COMPLETE — P5-T4R4 TEMPORAL ADAPTER CERTIFIED WITH ZERO PRIVILEGED LEAKAGE**

---

### 1. Investigation & Problem Statement

Prior research milestone P5-T4R4 generated 12,000 grasp episodes evaluating adaptive preload policies under multi-contact physical interaction (`experiments/adaptive_preload_validation_p5_t4r4/preload_raw_records.json`).

Before incorporating these historical experimental records into the World Model vNext training corpus, an architectural audit was mandated to determine whether they contain:
1. Per-step synchronized state-action-wrench trajectory tensors $(T, D)$, OR
2. Scalar episode summary statistics.

---

### 2. Empirical Audit of P5-T4R4 Raw Records

An automated inspection of `preload_raw_records.json` was conducted via `audit_p5_t4r4_raw_records()`:

```json
{
  "episode_id": "ep_4950000",
  "policy": "learned_adaptive",
  "seed": 4950000,
  "n_contacts": 4,
  "mu_min": 0.4688,
  "mass_kg": 0.8122,
  "planned_preload_N": 4.25,
  "mean_total_fn": 4.18,
  "impulse_total_Ns": 2.09,
  "gross_slip_step": null,
  "damage_proxy": 0.0,
  "success": true
}
```

#### Key Findings
1. **Scalar Summary Structure:** The 12,000 raw records store only scalar scalar outcomes (`mean_total_fn`, `impulse_total_Ns`, `damage_proxy`, `gross_slip_step`, `success`). They do NOT contain sequential observation arrays (`states`, `actions`, `wrenches`, `modes`).
2. **Training Hazard:** Treating scalar episode summaries as time-series data would corrupt temporal transformer training.
3. **Deterministic Replay Feasibility:** Every P5-T4R4 record contains its exact pseudorandom simulation `seed`, `n_contacts`, `mu_min`, `mass_kg`, and `policy`. Because the HOROS simulator (`NContactGraspEnvP5T3`) is bitwise deterministic given its seed, the complete per-step temporal trajectory can be deterministically reconstructed on demand.

---

### 3. Implementation of the Temporal Replay Adapter

To bridge this data contract gap safely, `scripts/p5_t4r4_temporal_adapter.py` was developed:

#### 3.1 Replay Architecture
- `replay_p5_t4r4_episode_to_temporal(record, env_steps=50)`:
  - Instantiates `NContactGraspEnvP5T3` parameterized with `n_contacts`, `mu_min`, and `mass_kg`.
  - Resets with the identical episode `seed`.
  - Executes the closed-loop policy (`learned_adaptive`, `fixed_5N`, `fixed_8N`, `fixed_12N`, etc.) over $T=50$ timesteps.
  - Synchronously records:
    - `states`: $(50, 18)$ — finger normal forces, positions, tangent velocities, workpiece state.
    - `actions`: $(50, 3)$ — commanded preload and impedance increments.
    - `wrenches`: $(50, 6)$ — 6-axis resultant contact wrench.
    - `modes`: $(50,)$ — contact mode sequence ($0 \dots 4$).

#### 3.2 Grounded Firewall Enforcement
The adapter strictly separates model-visible observations from privileged ground truth:
- Privileged parameters (`mu_true`, `mass_kg`, `planned_preload_N`, `oracle_labels`) are stripped from model-visible tensors.
- Channel-level validation (`assert_tensor_channels_clean`) verifies that no internal friction or mass series is copied into sensor channels.
- Output packages conform 100% to the approved World Model vNext data contract (`MODEL_VISIBLE_ALLOWLIST`).

---

### 4. Verification & Replay Invariance

The adapter was validated on test seeds (e.g., seed `4950001`):
- Regenerated $(50, 18)$ states, $(50, 3)$ actions, and $(50, 6)$ wrenches.
- Repeated replays produced bit-for-bit identical arrays and SHA-256 hashes (`384d8eb`).
- Zero reconstructable join keys or privileged blocklist tokens leaked into the model package.

### 5. Conclusion

The P5-T4R4 dataset cannot be ingested directly as scalar summaries, but the verified replay adapter provides a versioned, firewall-compliant bridge to deterministically convert any P5-T4R4 scenario into full World Model temporal tensors whenever required.
