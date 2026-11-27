# HOROS PROBE-1 — Stage 3.7C.4 Completion Report
## Cross-Runtime Security Acceptance & Evidence Closure

**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Workspace:** `C:\projects\horos-probe-1\Research-002`  
**Base Commit:** `9d57580df393d114cadcee21ce4d93275b962f40`  
**Branch:** `feat/probe1-stage37c4-cross-runtime-acceptance`  
**Milestone:** Stage 3.7C.4  

---

### 1. Executive Summary

Stage 3.7C.4 closes the remaining security-verification gaps from Stage 3.7C.3, delivering genuine cross-runtime enforcement, verified resource limits under cgroups v2, multi-route vault isolation, HIPC negative frame hardening, categorized governance attestation, and Ed25519 cryptographic provenance anchoring.

Zero model training, zero weight updates, and zero private holdout generation occurred during this milestone. All historical checkpoints, datasets, and quarantine records remain intact.

---

### 2. Workstream Implementation Summary

#### Workstream 1: Resource Enforcement (ISO-RES-01)
- **Cgroups v2 Quota Extraction:** Implemented in-worker extraction of effective CPU cores from `/sys/fs/cgroup/cpu.max` and `/proc/self/cgroup` (`quota / period`), `memory.max` in bytes, and `pids.max`.
- **Configured vs. Observed vs. Enforced:** Structured resource reporting to explicitly distinguish:
  - `configured_limits`: CPU: 2.0 cores, Memory: 2 GB (`2147483648` B), PIDs: 128.
  - `observed_limits`: empirically measured values from cgroup/rlimit interfaces.
  - `enforced`: boolean indicating whether measurements match quotas and are actively constrained.
- **Fail-Closed Rule:** Enforced that null or unavailable resource measurements can never receive `STATUS_TESTED_AND_VERIFIED`.

#### Workstream 2: Actual Runtime Integration
- **Execution Profiles:** Integrated `IsolatedInferenceWorker` with 4 distinct execution profiles:
  - `PROFILE_DOCKER_OCI` (`"docker_oci"`)
  - `PROFILE_WSL2_NAMESPACES` (`"wsl2_namespaces"`)
  - `PROFILE_NATIVE_NAMESPACES` (`"native_namespaces"`)
  - `PROFILE_SUBPROCESS_FALLBACK` (`"subprocess_fallback"`)
  - `PROFILE_AUTO` (`"auto"`): selects native Linux namespaces on POSIX, subprocess fallback on Windows host.
- **Attributable Tier Tracking:** `worker.get_active_runtime_tier()` records the exact runtime profile used for every execution, explicitly disallowing inference of isolation from host capability flags.

#### Workstream 3: Multi-Route Vault Isolation (ISO-VAULT-01)
- **Comprehensive Route Probing:** Implemented `test_vault_isolation()` probing 7 distinct attack vectors:
  1. Direct filesystem access
  2. Relative path traversal (`../../`)
  3. Symlink traversal (`/proc/self/cwd`)
  4. `/proc/1/root` access to protected vault path
  5. Alternate mount namespace visibility (`/proc/mounts`)
  6. Environment variable leakage
  7. Inherited file descriptor handle leakage (`/proc/self/fd`)
- **Conclusive Categorization:** Each route is strictly classified as one of: `DENIED`, `NOT_FOUND`, `UNSUPPORTED`, or `INCONCLUSIVE`.

#### Workstream 4: HIPC Protocol Hardening (ISO-IPC-01)
- **Unauthorized Command Rejection:** Rejects unknown or unauthorized commands with structured `{"status": "ERROR", "error": "Unauthorized or unhandled command: <cmd>"}`.
- **Negative Binary Frame Testing:** Validated fail-closed rejection of:
  - Malformed magic headers (`b"BADM"`)
  - Raw pickle opcodes (`\x80\x04`, `\x80\x05`, `PK\x03\x04`)
  - Oversized payloads (> 64 MB) and oversized headers (> 1 MB)
  - Invalid tensor shapes, negative dimensions, and non-finite values
- **Independent Nonce Tracking:** Validated client-side and worker-side replay rejection independently.

#### Workstream 5: Categorized Governance Attestation (ISO-GOV-01)
- Separated governance invariants into 4 distinct, tamper-evident sections:
  1. `runtime_verified_invariants`: dynamically checked flags (`training_permitted == 0`, `execution_authorized == 0`).
  2. `static_policy_declarations`: non-executable policy constants (`new_holdout_generation: "NOT_AUTHORIZED"`).
  3. `physical_artifact_quarantine`: physical verification that `stage35_final_holdout` is excluded from evaluation paths and permanently quarantined.
  4. `production_authorization_state`: production locks (`production_keys: "NOT_GENERATED"`, `production_vault: "NOT_INITIALIZED"`).

#### Workstream 6: Cryptographic Provenance Anchoring (ISO-CRYPTO-01)
- **Canonical Digest Binding:** Bound candidate checkpoint SHA-256, active runtime identity, security control evidence, evaluator context, governance invariants, and Git commit SHA into canonical SHA-256 digest `evidence_digest_sha256`.
- **Ed25519 Attestation Signature:** Cryptographically signed the canonical digest using a dedicated non-production test key (`TEST_NONPROD_PUBLIC_KEY`). Any mutation of controls, checkpoint, or governance invariants invalidates both digest and signature.

#### Workstream 7: Independent Regression Validation (P56)
- **New Test Suite:** Added `tests/test_p56_stage37c4_cross_runtime_acceptance.py` with 14 comprehensive test cases covering all 10 mandatory requirements.
- **Regression Status:**
  - P56 suite: 14/14 passed in 11.30s.
  - Full Stage 3.7 regression suite (P50–P56): **119/119 passed in 90.48s**.

---

### 3. Governance Invariant Audit

All non-negotiable governance constraints verified intact:
```text
training_permitted = 0
execution_authorized = 0
gradient_steps = 0
optimizer_updates = 0
new_holdout_generation = NOT_AUTHORIZED
production_keys = NOT_GENERATED
production_vault = NOT_INITIALIZED
final_evaluation = BLOCKED
compromised_holdout_id = stage35_final_holdout
compromised_holdout_status = COMPROMISED_PUBLIC_EXPOSURE
compromised_holdout_protocol = HOROS-P1-R002-STAGE3.5-FINAL_HOLDOUT
```

---

### 4. Cryptographic Provenance Record

- **Attestation Schema Version:** `2.0.0`
- **Milestone:** `Stage 3.7C.4 Cross-Runtime Security Acceptance & Evidence Closure`
- **Candidate Checkpoint SHA-256:** `45b421e91a85617c23afd9f5857df1141e0b731e7d175b3981eba6664dfa1cfc`
- **Canonical Evidence Digest (SHA-256):** `348db65fff3ba2dc5cff5bc6821aaaebd84b3a317f3a8ccd1053de3c9754911b`
- **Signature Scheme:** `Ed25519-NON_PRODUCTION_TEST_KEY`
- **Public Key (Hex):** `0094770222f703e2264b971a81498b3f68051790409a25b15be112d7f4be476b`
- **Attestation Signature:** Verified valid.
