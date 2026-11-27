# HOROS PROBE-1 — Stage 3.7C.5 Threat Model & Adversarial Analysis

## 1. System Overview & Scope
Stage 3.7C.5 establishes comprehensive **Security Attestation Trust Closure & Runtime Backend Acceptance** for HOROS PROBE-1 (`Research-002`).

The primary security objective is to eliminate all remaining architectural and verification gaps identified in independent audits and CodeRabbit review findings:
1. Enforce authentic Ed25519 digital signature validation for modern attestation schemas (2.0.0+), eliminating unsigned legacy digest fallback.
2. Cross-validate top-level provenance metadata against nested provenance dictionaries, preventing revision desynchronization.
3. Fail closed on explicit runtime profile requests (Docker OCI, WSL2 namespaces, native Linux namespaces) when container/namespace tooling is unavailable, preventing silent fallback to plain subprocesses.
4. Separate host checkpoint paths from worker/container checkpoint paths (`/model/checkpoint.pt` for OCI, `/mnt/<drive>/...` for WSL2).
5. Target specific sensitive files through `/proc/1/root` rather than testing root directory existence.
6. Integrate authoritative quarantine registry verification (`load_quarantine_record`) and recursively scan search paths for nested, renamed, or hash-matched compromised holdout artifacts.
7. Enforce fail-closed governance invariant validation covering all mandatory restrictions.

---

## 2. Adversary Profile & Assumptions
- **Attacker Capabilities:**
  - Arbitrary code execution within the worker process context.
  - Ability to craft arbitrary byte sequences across the IPC communication channel.
  - Ability to attempt raw syscalls (`setuid`, `socket`, `clone`, `fork`, `ptrace`, filesystem writes).
  - Ability to inspect `/proc`, `/sys`, environment variables, open file descriptors, and mount namespaces.
  - Ability to manipulate attestation records (stripping signatures, substituting untrusted public keys, modifying governance invariants, or substituting legacy 3-parameter digests).
- **Attacker Constraints:**
  - Evaluator execution runs in a strictly higher-privilege security domain with exclusive access to the private vault and cryptographic keys.
  - Host kernel and hypervisor security boundaries are assumed uncompromised.
  - Production keys remain `NOT_GENERATED` and production vault remains `NOT_INITIALIZED`.

---

## 3. Threat Vectors & Mitigations

| Threat Vector | Attack Mechanism | Evaluator/Worker Mitigation | Validation Method |
| :--- | :--- | :--- | :--- |
| **TV-01: Attestation Signature Stripping & Legacy Digest Substitution** | Removing `attestation_signature` from a schema 2.0.0+ record and computing legacy 3-parameter digest. | Modern schemas (`>= 2.0.0`) strictly require a valid Ed25519 signature and full payload digest; legacy 3-parameter digest fallback is isolated exclusively to legacy schema `1.0.0`. | `test_p57::test_01` asserts that signature deletion and legacy digest substitution fails closed with `ContractViolationError`. |
| **TV-02: Public-Key Substitution & Signature Forgery** | Signing an attestation with an attacker-generated Ed25519 keypair and substituting `signature_public_key_hex`. | Attestation validator verifies that `signature_scheme == "Ed25519-NON_PRODUCTION_TEST_KEY"` and `signature_public_key_hex == TEST_NONPROD_PUBLIC_KEY_HEX`. Untrusted public keys are rejected. | `test_p57::test_02` asserts attacker public key substitution and corrupted signature bytes fail verification. |
| **TV-03: Source Revision Desynchronization** | Mutating top-level `source_commit_sha` while leaving `provenance.source_commit_sha` intact (or vice-versa). | Strict provenance cross-validation verifies that `record["source_commit_sha"] == prov["source_commit_sha"]`, `attestation_id == prov["attestation_uuid"]`, and metadata matches. | `test_p57::test_03` asserts that top-level or provenance revision modification fails validation. |
| **TV-04: Silent Isolation Downgrade** | Requesting `docker_oci` or `wsl2_namespaces` when unavailable, causing worker to silently fall back to unisolated subprocess. | Worker initialization verifies engine availability (`docker_available`, `wsl_unshare_available`) and fails closed with `ContractViolationError` if the requested profile cannot be started. | `test_p57::test_09` verifies that requesting OCI or WSL2 when unavailable raises `ContractViolationError`. |
| **TV-05: Checkpoint Mount Desynchronization** | Sending host checkpoint paths (`/tmp/...`, `C:\...`) to container workers where the mount is located at `/model/checkpoint.pt`. | Worker client verifies bytes against host path, then sends container path (`/model/checkpoint.pt` for OCI, `/mnt/<drive>/...` for WSL2) across IPC to worker. | `test_p57::test_07` and `test_08` verify OCI and WSL2 path translations. |
| **TV-06: Procfs False-Positive Accessibility** | Reporting `/proc/1/root` as `ACCESSIBLE` merely because `/proc/1/root` directory exists, even when vault is inaccessible. | Probe targets the specific protected vault target under `/proc/1/root` (`/proc/1/root/<vault_rel>/<target_name>`); reports `ACCESSIBLE` only if the sensitive file is actually readable. | `test_p57::test_11` verifies that `/proc/1/root` reports `NOT_FOUND`/`DENIED` when protected target is unexposed. |
| **TV-07: Nested / Renamed Compromised Holdout Exposure** | Hiding compromised Stage 3.5 holdout files under subdirectories or renamed extensions. | `verify_physical_holdout_quarantine()` uses `load_quarantine_record()` to fetch authentic digests and recursively scans search paths matching filenames and content SHA-256 hashes. | `test_p57::test_10` verifies detection of nested and renamed compromised holdout files. |
| **TV-08: Governance Policy Bypass** | Declaring `new_holdout_generation="AUTHORIZED"`, nonzero gradient steps, or unblocked evaluation. | Schema validator enforces all governance invariants (`training_permitted=0`, `execution_authorized=0`, `gradient_steps=0`, `optimizer_updates=0`, `new_holdout_generation=NOT_AUTHORIZED`, `final_evaluation=BLOCKED`, `production_keys=NOT_GENERATED`, `production_vault=NOT_INITIALIZED`). | `test_p57::test_04` asserts that tampering with any governance policy field raises `ContractViolationError`. |

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
