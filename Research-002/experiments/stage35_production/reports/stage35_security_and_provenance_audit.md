# HOROS PROBE-1 — Research-002
## Stage 3.5 Deliverable 08: Security, CSPRNG Provenance & Anti-Leakage Audit

**Program:** HOROS Physical Interaction Data Engine (PIDE)  
**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Stage:** Stage 3.5 — Scientific Gate Closure & HCTT-256 Training Readiness  
**Document Identifier:** `HOROS-P1-R002-STAGE35-REP-08-SECURITY-AUDIT`  
**Date:** 2026-11-25  

---

### 1. Stage 3.5 Runtime Salt Rotation (Zero Source-Code Literals)

Stage 3.3 used a fixed deterministic salt (`STAGE3_DETERMINISTIC_AUDIT_SALT`) embedded for replay audit testing. For Stage 3.5 datasets (`prelift_long` and `final_holdout`), `scripts/run_stage35_pipeline.py` enforces `resolve_stage35_runtime_salt()`:
1. Reads a hex key from environment variable `HOROS_STAGE35_AUDIT_SALT_HEX` ($\ge 16$ bytes) if externally provisioned, **OR**
2. Generates a fresh 256-bit cryptographically random key at runtime via `os.urandom(32)` (`salt_source = "runtime_os_urandom_32bytes"`).
3. Records only the non-secret SHA-256 digest `audit_salt_sha256` (`864dd3d00a67b7edfcadc9c4bd99065b49c2fffd22368bfe353af0874a45344b`) in `audit/stage35_audit_manifest.json` and per-episode `.privileged.json` files — **never writing raw key bytes or hardcoded salt literals into git-tracked source or report artifacts**.

Regression tests in `tests/test_p44_stage35_gate_closure.py` programmatically verify:
- `STAGE3_DETERMINISTIC_AUDIT_SALT` is absent from `scripts/run_stage35_pipeline.py`.
- Zero 64-character hex string literals exist in `scripts/run_stage35_pipeline.py`.
- Live `os.urandom(32)` salt generation succeeds on isolated `.privileged.json` manifests and fails closed (`ContractViolationError`) when queried with any mismatched salt.

---

### 2. Historical Archive Preservation & Cryptographic Integrity Scope

- **Stage 3.3 Historical Archives Unmodified:** To preserve historical reproducibility without rewriting archived files, all `1,000` Stage 3.3 `.npz` and `.privileged.json` files keep their original `sample_id` values:
  - `experiments/stage3_production/accepted_manifest.json` SHA-256: `45f961fce05b764968f699c9944c16c4f25c25a2a29e48a7c863c3997d51f2fd` (unchanged).
  - `experiments/stage3_production/stage34_episode_split_manifest.json` SHA-256: `6b34cd67029bb1fae6692b82a74571801797f7b84a89130955f6e8f3c7633c2d` (unchanged).
- **Multi-Root Verification:** `AuditTargetLoader([s33_root, s35_root / "prelift_long"])` verifies `accepted_manifest.json` counts, per-episode `provenance_hash` equality, `audit_salt_sha256` consistency within each root, and mode vocabulary (`0..4`), while single-root loading with runtime `audit_salt` verifies full HMAC-SHA256 bijection.
- **Honest Security Scope Statement:** HMAC sample-ID binding prevents unprivileged NPZ readers from guessing canonical episode identifiers (`canonical_episode_id` / `seed`) only when the runtime salt remains secret outside the repository (as in Stage 3.5 `runtime_os_urandom_32bytes`); legacy Stage 3.3 archives remain cryptographically verifiable for provenance integrity (`SHA-256` checksums) while acknowledging that Stage 3.3 used an openly committed deterministic audit salt.

---

### 3. Model-Input Firewall Verification

- `validate_model_visible_package`, `assert_tensor_channels_clean`, `assert_no_privileged_keys_in_inputs`, and `HCTTWorldModel.forward` continue to reject all privileged keys (`modes`, `target_modes`, `friction_mu`, `mu_true`, `step_mu_true`, `mass_kg`, `clearance_mm`, `rewards`, `privileged_audit`) fail-closed with `ContractViolationError`.
- `stage35_dataset_manifest.json` records SHA-256 checksums for all `1,000` files in `prelift_long/` (`500` NPZ + `500` `.privileged.json`) and all `240` files in `final_holdout/` (`120` NPZ + `120` `.privileged.json`).
