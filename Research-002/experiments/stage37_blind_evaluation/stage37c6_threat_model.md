# HOROS PROBE-1 — Stage 3.7C.6 Threat Model & Adversarial Analysis
## Attestation Acceptance Hardening & Independent Runtime Verification

## 1. System Overview & Scope
Stage 3.7C.6 establishes comprehensive **Attestation Acceptance Hardening & Independent Runtime Verification** for HOROS PROBE-1 (`Research-002`).

The primary security objective is to eliminate all remaining architectural and verification gaps identified in independent audits and CodeRabbit review findings from PR #45:
1. **Separate Test-Key Signing from Production Acceptance:** Isolate test key signing to explicit test utilities; production acceptance unconditionally rejects `Ed25519-NON_PRODUCTION_TEST_KEY` and untrusted public keys.
2. **Cryptographic Binding of Attestation Envelope:** Cryptographically bind `schema_version`, `milestone`, `attestation_id`, `generated_at_utc`, `source_commit_sha`, `signature_scheme`, `signature_public_key_hex`, `provenance`, and evidence into canonical UTF-8 SHA-256 digest.
3. **Downgrade Attack Prevention:** Schema `1.0.0` unsigned fallback requires explicit caller policy; modern attestation records cannot downgrade themselves.
4. **Authoritative Quarantine Verification:** Detect compromised holdout artifacts using authoritative registry getters (`get_compromised_npz_sha256()`, `get_compromised_privileged_sha256()`, `get_compromised_sample_ids()`) and track scan completeness (`scan_complete`). Incomplete scans fail closed.
5. **Runtime Identity vs Control Consistency:** Control isolation tier must match the independently verified worker runtime identity (`active_runtime_tier`), preventing capability substitution.
6. **Docker vs Podman Engine Correctness:** Dynamically select between Docker and Podman based on actual engine preflight checks, and pass the selected engine to `build_oci_run_command`.
7. **Governance Evidence Reconciliation:** Reconcile `ISO-GOV-01` to record numeric `0` for `training_permitted` and `execution_authorized`.

---

## 2. Adversary Profile & Assumptions
- **Attacker Capabilities:**
  - Arbitrary code execution within the worker process context.
  - Ability to craft arbitrary byte sequences across the IPC communication channel.
  - Ability to attempt raw syscalls (`setuid`, `socket`, `clone`, `fork`, `ptrace`, filesystem writes).
  - Ability to inspect `/proc`, `/sys`, environment variables, open file descriptors, and mount namespaces.
  - Ability to manipulate attestation records (stripping signatures, substituting untrusted public keys, modifying governance invariants, substituting legacy 3-parameter digests, or downgrading schema version).
- **Attacker Constraints:**
  - Evaluator execution runs in a strictly higher-privilege security domain with exclusive access to the private vault and cryptographic keys.
  - Host kernel and hypervisor security boundaries are assumed uncompromised.
  - Production keys remain `NOT_GENERATED` and production vault remains `NOT_INITIALIZED`.

---

## 3. Threat Vectors & Mitigations

| Threat Vector | Attack Mechanism | Evaluator/Worker Mitigation | Validation Method |
| :--- | :--- | :--- | :--- |
| **TV-01: Public Test-Key Production Acceptance** | Submitting an attestation signed with the publicly recoverable test seed to a production evaluator. | `verify_production_attestation_acceptance()` strictly rejects `Ed25519-NON_PRODUCTION_TEST_KEY` and untrusted public keys, requiring explicitly configured production trust anchors. | `test_p58::test_p58_01` and `test_p58_22` assert test signatures fail production acceptance. |
| **TV-02: Attestation Schema Downgrade** | Modifying `schema_version` from `2.2.0` to `1.0.0`, removing the signature, and computing a legacy 3-parameter digest. | Records cannot select their own weaker verification policy; schema 1.0.0 requires explicit caller authorization (`allow_legacy_unsigned=True`). | `test_p58::test_p58_03` asserts downgrade fails closed. |
| **TV-03: Envelope & Provenance Mutation** | Altering `schema_version`, `milestone`, `attestation_id`, or `source_commit_sha` after signature generation. | All envelope fields are cryptographically bound into `evidence_digest_sha256`. Any mutation invalidates both digest and signature. | `test_p58::test_p58_04` asserts envelope field mutation fails validation. |
| **TV-04: Untrusted Key Substitution** | Generating an arbitrary Ed25519 keypair, signing the payload, and inserting the attacker public key. | Signature public key must match trusted anchors in policy. Test pathway requires `TEST_NONPROD_PUBLIC_KEY_HEX`; production requires anchored production keys. | `test_p58::test_p58_06` asserts key substitution fails closed. |
| **TV-05: Dirty / Unknown Source Provenance** | Generating attestation records from dirty git trees or uncommitted revisions. | Source tree digest is computed deterministically; dirty git trees and `UNKNOWN_UNCOMMITTED_REVISION` are strictly rejected by production acceptance. | `test_p58::test_p58_07` and `test_p58_08` verify provenance enforcement. |
| **TV-06: Authoritative Quarantine Bypass** | Modifying filenames or hiding holdout files in subdirectories or copied directories. | Recursive scan matches against authoritative registry digests (`get_compromised_npz_sha256`, `get_compromised_privileged_sha256`, `get_compromised_sample_ids`). Copied quarantine folders are scanned. | `test_p58::test_p58_09`, `test_p58_10`, `test_p58_11` verify detection. |
| **TV-07: Incomplete Quarantine Scan Concealment** | Introducing unreadable or oversized candidates to bypass content inspection while claiming verified clean status. | Any unreadable or oversized eligible file sets `scan_complete=False` and `physical_exclusion_verified=False`. Incomplete scans fail closed. | `test_p58::test_p58_12` asserts incomplete scans reject verification. |
| **TV-08: Isolation Tier Substitution** | Claiming `docker_oci` in control evidence while the actual worker runs as `subprocess_fallback`. | Schema validator strictly checks that control evidence tier matches the independently verified `active_runtime_identity`. | `test_p58::test_p58_14` and `test_p58_23` assert tier mismatches fail closed. |
| **TV-09: Silent OCI Fallback & Wrong Engine Selection** | Requesting OCI in environments with only Podman available or neither available. | Worker selects `podman` when Docker is missing and fails closed when neither is available. Engine is explicitly passed to CLI command builder. | `test_p58::test_p58_18`, `test_p58_19`, `test_p58_20` verify engine selection. |
| **TV-10: Governance Evidence Inconsistency** | Storing boolean `True` for numeric authorization flags or contradictory policy values. | Schema validator enforces numeric `0` for `training_permitted` and `execution_authorized`. Boolean representations fail closed. | `test_p58::test_p58_17` asserts governance contradictions fail closed. |

---

## 4. Research Governance Invariants
All operations maintain absolute compliance with non-negotiable research governance:
```text
training_permitted = 0
execution_authorized = 0
gradient_steps = 0
optimizer_updates = 0

new_holdout_generation = NOT_AUTHORIZED
production_keys = NOT_GENERATED
production_vault = NOT_INITIALIZED
final_evaluation = BLOCKED

stage35_holdout = PERMANENTLY_COMPROMISED
```
