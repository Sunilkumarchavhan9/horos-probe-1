# HOROS PROBE-1 — Stage 3.7C.6 Security Control Matrix
## Attestation Acceptance Hardening & Independent Runtime Verification

## Control Verification Matrix

| Control ID | Control Name | Enforcement Boundary | Implementation Mechanism | Verification Requirement | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **ISO-CRYPTO-01** | Test-Key Signing Isolation & Production Rejection | Attestation Engine | Explicit separation of test-signing pathway (`sign_attestation_digest_test_only`); production acceptance strictly rejects `Ed25519-NON_PRODUCTION_TEST_KEY`. | Test records pass only through authorized test path; untrusted public keys and test signatures fail production acceptance. | `TESTED_AND_VERIFIED` |
| **ISO-CRYPTO-02** | Complete Envelope Cryptographic Binding | Attestation Builder & Validator | Full cryptographic binding of `schema_version`, `milestone`, `attestation_id`, `generated_at_utc`, `source_commit_sha`, `signature_scheme`, and `signature_public_key_hex`. | Any tampering with schema version, envelope identifiers, or metadata invalidates evidence digest and fails verification. | `TESTED_AND_VERIFIED` |
| **ISO-PROV-01** | Source Tree Integrity & Dirty Provenance Rejection | Provenance Module | Deterministic SHA-256 source tree hashing (`compute_source_tree_digest`) and git dirty state inspection (`is_source_tree_dirty`). | Clean committed revision required for production acceptance; dirty trees and unknown revisions fail closed. | `TESTED_AND_VERIFIED` |
| **ISO-DOWNGRADE-01** | Anti-Downgrade Schema Verification Policy | Trust Policy Engine | Modern schema verification is default; legacy unsigned schema 1.0.0 requires explicit caller authorization (`allow_legacy_unsigned=True`). | Attackers cannot bypass signature validation by mutating schema version to 1.0.0. | `TESTED_AND_VERIFIED` |
| **ISO-QUAR-01** | Authoritative Registry Quarantine Verification | Quarantine Engine | Dynamic integration with authoritative getters (`get_compromised_npz_sha256`, `get_compromised_privileged_sha256`, `get_compromised_sample_ids`). | Recursive scanning detects nested, renamed, and copied quarantine directories. Incomplete scans fail closed. | `TESTED_AND_VERIFIED` |
| **ISO-TIER-01** | Runtime Identity & Control Tier Consistency | Attestation Schema Validator | Strict matching between control isolation tier and independently verified `active_runtime_identity`. | Capability availability cannot substitute for worker isolation; tier mismatches fail closed with `ContractViolationError`. | `TESTED_AND_VERIFIED` |
| **ISO-ENGINE-01** | Dynamic OCI Engine Selection (Docker/Podman) | Worker Isolation Client | Dynamic discovery with Docker preference and automatic Podman selection; explicit engine argument to `build_oci_run_command`. | Fails closed if neither engine is available; preserves read-only, `--network none`, `--cap-drop ALL`, and `/model/checkpoint.pt:ro`. | `TESTED_AND_VERIFIED` |
| **ISO-UID-01** | Worker Identity & Escalation Denial Enforcement | In-Worker Isolation Probes | Empirical verification of non-root UID/GID (10001:10001), privilege escalation denial, and `no_new_privs` state. | Missing UID or failed escalation denial rejects verified status; `no_new_privs` alone cannot substitute. | `TESTED_AND_VERIFIED` |
| **ISO-RES-01** | Cgroups v2 Resource Quota Enforcement | Kernel Cgroups / Worker Probes | Enforcement of effective CPU cores (<= 2.0), memory (<= 2 GB), and PIDs (<= 128) via cgroups v2. | Distinguishes configured, observed, and enforced limits; rejects null or over-quota limits from verified status. | `TESTED_AND_VERIFIED` |
| **ISO-NET-01** | Kernel Network Namespace Isolation | Kernel / OCI Container | Network namespace (`--network none`, `unshare -n`) and capability dropping (`--cap-drop=ALL`). | Requires actual recognized isolated tier (`docker_oci`, `native_namespaces`, `wsl2_namespaces`); rejects `subprocess_fallback`. | `TESTED_AND_VERIFIED` |
| **ISO-VAULT-01** | Multi-Route Private Vault Invisibility | In-Worker Isolation Probes | Traversal probing across direct paths, relative paths, symlinks, `/proc/1/root`, mount namespaces, env, and handles. | Target-specific probing under `/proc/1/root`; conclusive classification of `DENIED`, `NOT_FOUND`, `UNSUPPORTED`. | `TESTED_AND_VERIFIED` |
| **ISO-GOV-01** | Strict Numeric Governance Invariant Verification | Schema Validator | Enforces integer `0` for `training_permitted` and `execution_authorized`. | Contradictory declarations or boolean `True` representations fail closed. | `TESTED_AND_VERIFIED` |
| **ISO-IPC-01** | Safe Binary IPC Protocol (HIPC) | Binary IPC Codec | Typed binary frames with length-prefixing, raw contiguous numpy tensor buffers, and zero pickle deserialization. | Corrupted magic (`b"BADM"`), raw pickle (`b"\x80\x04"`, `PK\x03\x04`), oversized headers, and oversized payloads rejected. | `TESTED_AND_VERIFIED` |
| **ISO-REPLAY-01** | Nonce Tracking & Replay Attack Denial | IPC Client / Worker Filter | Independent per-request cryptographic nonce tracking on both evaluator and worker boundaries. | Duplicate request nonces rejected fail-closed on both evaluator client and worker server boundaries. | `TESTED_AND_VERIFIED` |

---

## Governance Invariant Attestation
- `training_permitted = 0`
- `execution_authorized = 0`
- `gradient_steps = 0`
- `optimizer_updates = 0`
- `new_holdout_generation = NOT_AUTHORIZED`
- `production_keys = NOT_GENERATED`
- `production_vault = NOT_INITIALIZED`
- `final_evaluation = BLOCKED`
- `stage35_holdout = PERMANENTLY_COMPROMISED`
