# HOROS PROBE-1 — Research-002
## Stage 3.4 Deliverable 04: Secure Supervised-Target Separation & Security Review

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.4 — HCTT-256 Implementation and Temporal Training-Pipeline Completion  
**Implementation:** `src/world_model_vnext/target_loader.py` (`AuditTargetLoader`) & `src/world_model_vnext/splits.py`  

---

### 1. Supervised Target Separation Architecture

Stage 3.4 isolates inference-visible inputs from privileged simulator supervision across module, storage, and runtime boundaries:

| Pipeline Stage | Permitted Keys | Excluded Keys | Module Boundary |
| :--- | :--- | :--- | :--- |
| **1. Model-Visible NPZ Loader** | `states`, `actions`, `next_states`, `wrenches`, `next_wrenches`, `sample_id`, `task` | `modes`, `friction_mu`, `mass_kg`, `clearance_mm`, `seed`, `rewards` | `dataset.py` |
| **2. `HCTTWorldModel.forward()`** | `task`, `hist_states`, `hist_prev_actions`, `hist_wrenches`, `hist_mask`, `future_actions`, `future_action_mask`, optional model `prev_mode_probs` | All simulator ground truth + targets | `hctt.py` |
| **3. `AuditTargetLoader` (Loss/Eval Only)** | Joins `sample_id` to `modes` (`0..4`), `step_mu_true`, `step_object_mass_kg`, `seed`, `stratum` | Never imported or called by `hctt.py` or `dataset.py` | `target_loader.py` |

#### Fail-Closed Target Integrity Checks
`AuditTargetLoader` verified all **1,000 / 1,000** `.privileged.json` files against `accepted_manifest.json` and `stage3_3_audit_manifest.json`:
- **HMAC & Salt Verification:** Recomputes `HMAC-SHA256(audit_salt, canonical_episode_id)` for every episode and matches `sample_id` and `audit_salt_sha256`.
- **Bijection & Duplicate Rejection:** Fails closed (`ContractViolationError`) on missing `.privileged.json`, duplicate `sample_id`/`seed`/`canonical_episode_id`, or provenance hash mismatches.
- **5-Mode Operational Mapping:** Validates that all 25,000 step mode labels lie strictly in `{0: Free, 1: FirstContact, 2: Sticking, 3: Sliding, 4: Impact}` (`{0: 8445, 1: 561, 2: 12936, 3: 2670, 4: 388}`). Explicitly notes `Jamming (5)` and `Release (6)` have `0` dataset instances and are not fabricated.

---

### 2. Security Audit of Existing Deterministic HMAC Salt

**Explicit Finding:** In `scripts/run_stage3_production_dataset.py` line 73, `STAGE3_DETERMINISTIC_AUDIT_SALT` is initialized from a literal committed in source code (`hashlib.sha256(b"HOROS_STAGE3_PRODUCTION_AUDIT_SALT_2026").digest()`).

- **Honest Threat Assessment:** Because the salt literal is readable to anyone with repository access, the current `sample_id` filenames are **pseudonymized deterministic identifiers, NOT cryptographically unlinkable secrets**. Any reader with both `stage3_3_seed_registry.json` and the committed salt could recompute the mapping to `canonical_episode_id`.
- **Why Existing 1,000 Archives Were Not Mutated:** Silently renaming or rewriting the 1,000 committed NPZ/audit archives would invalidate Stage 3.3 SHA-256 manifest provenance.
- **Safe Migration Strategy (Post-Stage 3.4 / Pre-External Release):**
  1. Keep `STAGE3_DETERMINISTIC_AUDIT_SALT` strictly as a legacy reproducibility fixture in CI tests only (`allow_legacy_committed_salt=True` opt-in).
  2. For future dataset campaigns or restricted evaluation splits, source the 32-byte salt exclusively from an environment/secret store (`HOROS_AUDIT_SALT_HEX`) outside Git, storing only `audit_salt_sha256` inside the audit manifest and rotating `sample_id` filenames via an explicit versioned migration manifest.
