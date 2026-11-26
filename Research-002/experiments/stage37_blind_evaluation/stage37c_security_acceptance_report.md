# HOROS PROBE-1 — Stage 3.7C: Production Security Acceptance Remediation Report

**Program:** HOROS PROBE-1 — Autonomous Robotic Manipulation World Model  
**Workspace:** `Research-002`  
**Repository:** `Sunilkumarchavhan9/horos-probe-1`  
**Base Commit:** `728b01fecb2fed87c4bb088a8e009f7f96fe4b0b` (Stage 3.7B PR #38)  
**Date:** 2026-11-26  
**Status:** REMEDIATED & CONDITIONALLY ACCEPTED (PRODUCTION EVALUATION BLOCKED)

---

## 1. Executive Summary & Objective

Stage 3.7B integrated the blind evaluation components (durable ledger lock, Ed25519 manifest verification, IPC validation, and child worker execution) with 455 regression tests passing. However, an independent audit identified critical security and architectural gaps in the actual call paths:
1. **Flawed burn-on-access ordering:** In `evaluator.py`, `synthetic_dataset_loader_fn()` was invoked *before* persisting `DATA_ACCESSED` to the durable ledger, meaning private data could be read without durable burn if a failure or crash occurred during loading.
2. **In-process evaluation bypass:** `evaluate_blind_holdout_release` accepted `use_isolated_worker: bool = False`, allowing callers to bypass OS isolation entirely.
3. **Caller-controlled Ed25519 trust override:** `verify_signed_evaluation_manifest` accepted caller-supplied `trusted_keys`, violating independent trust-root anchoring.
4. **Unrestricted pickle deserialization:** IPC streams utilized `torch.load(..., weights_only=False)` and pickle serialization, creating arbitrary code execution vulnerability across process boundaries.
5. **No checkpoint artifact binding:** Worker created models from random weight initialization rather than verifying SHA-256 digests and loading frozen candidate checkpoint bytes.
6. **Structurally valid ledger tampering vulnerability:** `_verify_ledger_integrity` only checked JSON schema, permitting schema-valid status reversions (e.g. `CONSUMED` -> `RESERVED`) and audit trail truncation.
7. **Overstated security claims:** Stage 3.7B claimed verified OS-level network denial and separate worker security principals on Windows, which were not backed by OS kernel enforcement.

Stage 3.7C resolved all code-level vulnerabilities, enforced mandatory isolated inference with strict candidate checkpoint loading, eliminated pickle deserialization via the typed safe `HIPC` binary protocol, established cryptographic state HMAC and hash-chained audit logging, and honestly reclassified operating system boundaries.

---

## 2. Remediation Analysis Across Workstreams

### Workstream A: Correct Burn-on-Access Ordering
- **Remediation:** In `evaluator.py`, `evaluate_blind_holdout_release` now executes `ledger.record_private_data_access(release_id)` and sets `data_accessed = True` *strictly before* invoking `synthetic_dataset_loader_fn()`.
- **Crash / Failure Semantics:** If decompression, decryption, or dataset loading fails, the exception is caught, `ledger.mark_aborted(release_id)` is persisted, and the release remains permanently burned (`ABORTED`, `private_data_accessed = True`). Any subsequent reservation or evaluation attempt fails closed with `HoldoutReleaseAlreadyConsumedError`.
- **Verification:** Verified by `test_01_burn_is_persisted_before_private_loader_invocation` and `test_02_loader_failure_after_access_permanently_burns_release`.

### Workstream B: Operating-System Identity & Vault Isolation Audit
- **Audited Boundary:** On Windows runners without elevation (`SeImpersonatePrivilege` or local Administrator rights), child processes spawned via `subprocess.Popen` execute under the same primary user security token and user SID (`S-1-5-21-...`) as the parent evaluator.
- **Honest Finding:** Applying NTFS deny ACLs via `icacls` to `%USERNAME%` restricts both the child worker and the parent evaluator. Genuine OS security principal separation requires dedicated service accounts, Windows AppContainers, or elevated `CreateRestrictedToken` / `CreateProcessAsUser` capability.
- **Classification:** Mark **WORKER_ONLY_VAULT_DENIAL: PARTIALLY_BLOCKED / AUDITED**. The code queries native Windows process token SIDs via `advapi32.GetTokenInformation` and strips vault environment variables, but real multi-user OS isolation is kept formally blocked pending an isolated host/VM execution environment.

### Workstream C: Real Network Isolation Boundary
- **Audited Boundary:** `HTTP_PROXY=0.0.0.0:0` and `HTTPS_PROXY=0.0.0.0:0` are environment variables respected by proxy-aware HTTP clients, but do not block raw socket calls (`socket.connect`).
- **Honest Finding:** Windows Defender Firewall rules and Windows Filtering Platform (WFP) callouts require elevated Administrator rights to modify. In standard CI/runner environments, true network denial must be enforced by runner-level network virtualization (e.g., container `--network none` or isolated VM hypervisor).
- **Classification:** Mark **ACTUAL_NETWORK_DENIAL: BLOCKED_PENDING_CONTAINER_NETWORK_NONE**. Proxy settings are classified strictly as defense-in-depth, not primary OS kernel enforcement.

### Workstream D: Mandatory Isolated Worker & In-Process Bypass Elimination
- **Remediation:** Removed the in-process fallback from `evaluate_blind_holdout_release`. Any attempt by callers to pass `use_isolated_worker=False` or `in_process=True` immediately raises `ContractViolationError`.
- **Verification:** Tested by `test_05_blind_evaluation_cannot_bypass_isolated_worker`. Production blind evaluation strictly requires `IsolatedInferenceWorker`.

### Workstream E: Candidate Checkpoint Artifact Binding & Safe Loading
- **Remediation:** Evaluator and worker now enforce explicit candidate model artifact binding:
  1. Computes SHA-256 over exact checkpoint file bytes on disk.
  2. Compares against expected candidate digest; rejects mismatch before worker invocation.
  3. Commands worker to load checkpoint via IPC `LOAD_CHECKPOINT`.
  4. Worker reads bytes, re-verifies SHA-256, and calls `torch.load(..., weights_only=True)`.
  5. State dict loaded with `strict=True` into `HCTTWorldModel(LOCKED_HCTT_CONFIG)`.
  6. Worker rejects inference if candidate checkpoint has not been verified and loaded.
- **Verification:** Tested by `test_10_checkpoint_bytes_match_expected_digest`, `test_11_modified_checkpoint_is_rejected`, and `test_12_worker_receives_verified_candidate_weights`.

### Workstream F: Immutable Ed25519 Trust Material
- **Remediation:** Removed caller-controlled `trusted_keys` parameter from `verify_signed_evaluation_manifest` and `evaluate_blind_holdout_release`. Any caller passing `trusted_keys` is rejected with `ContractViolationError`.
- **Trust Root:** Verification strictly resolves public keys from the internal `_TRUSTED_EVALUATION_PUBLIC_KEYS` registry populated by configuration, rejecting arbitrary caller-supplied public keys.
- **Verification:** Tested by `test_03_caller_supplied_ed25519_keys_cannot_authenticate_manifests` and `test_04_production_verifier_has_no_caller_controlled_trust_key_override`.

### Workstream G: Safe Binary IPC Serialization (HIPC Protocol)
- **Remediation:** Completely eliminated `torch.save` / `torch.load(..., weights_only=False)` and pickle serialization across IPC channels.
- **Protocol:** Implemented `HIPC` (HOROS IPC) protocol:
  - 4-byte magic `b"HIPC"` header.
  - Total payload length and JSON header length prefixes (uint32).
  - Schema-constrained JSON metadata header (tensors, shapes, dtypes, task).
  - Contiguous raw binary tensor buffers reconstructed via `np.frombuffer` and `torch.from_numpy`.
  - Enforced max message size (64 MB), non-negative dimensions, allowlisted keys, and strict numeric finiteness (`torch.isfinite(t).all()`).
- **Verification:** Tested by `test_13_ipc_does_not_use_unrestricted_pickle_deserialization` and `test_14_malformed_and_oversized_ipc_messages_fail_closed`.

### Workstream H: Durable Ledger Tamper Evidence & Cryptographic Hash-Chain
- **Remediation:**
  - `holdout_evaluation_ledger.json` now includes `state_hmac`: HMAC-SHA256 across canonical JSON bytes of `releases`. Any manual status tampering (e.g. `CONSUMED` -> `RESERVED`) breaks HMAC verification and fails closed.
  - `evaluation_audit_trail.jsonl` implements a cryptographic hash chain: each record includes sequence number `event_seq`, previous line's SHA-256 `prev_hash`, and HMAC signature `event_hmac`.
  - Deleting, inserting, or reordering audit log records breaks sequence numbers and hash linking, failing closed.
  - Persistence utilizes write-to-temp, `f.flush()`, `os.fsync()`, and atomic `os.replace()`.
- **Verification:** Tested by `test_15_schema_valid_ledger_tampering_is_detected` and `test_16_event_deletion_and_reordering_are_detected`.

### Workstream I: Canonical Manifest Serialization (HOROS-CANONICAL-JSON-V1)
- **Remediation:** Declared and implemented `HOROS-CANONICAL-JSON-V1`:
  - Deterministic lexicographic key ordering across all nesting levels.
  - Compact separators `(",", ":")` with zero whitespace.
  - Direct UTF-8 byte encoding (`ensure_ascii=False`) preserving Unicode characters without `\uXXXX` escaping.
  - Strict rejection of non-finite numbers (`NaN`, `Infinity`).
- **Verification:** Tested by `test_17_canonical_manifest_serialization_passes_declared_standard_test_vectors`.

---

## 3. Full Regression Suite Verification

The complete regression suite covering historical phases (P40 through P51) plus the new Stage 3.7C suite (P52) was executed:
- **Baseline Tests (P40–P51):** 455 tests passed.
- **Stage 3.7C Acceptance Suite (P52):** 20 tests passed.
- **Total Repository Tests:** 475 tests passed, 0 failures, 0 skipped.

---

## 4. Governance & Scientific Invariants

```text
training_permitted = 0
execution_authorized = 0
gradient_steps = 0
optimizer_updates = 0

new_holdout_generation = NOT_AUTHORIZED
new_seed_generation = NOT_AUTHORIZED
final_evaluation = BLOCKED

production_keys = NOT_GENERATED
production_vault = NOT_INITIALIZED
stage35_holdout = PERMANENTLY_COMPROMISED
```

- No HCTT-256 model training was conducted.
- No production seeds or holdout datasets were generated.
- No production cryptographic keys or vaults were created.
- All historical checkpoints, datasets, and quarantine guards remain intact.
