# HOROS PROBE-1 — Stage 3.7C.5 Security Control Matrix

## Control Verification Matrix

| Control ID | Control Name | Enforcement Boundary | Implementation Mechanism | Verification Requirement | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **ISO-CRYPTO-01** | Authentic Digital Signature Verification | Attestation Validator | Ed25519 signature over canonical UTF-8 evidence digest using dedicated non-production test key (`TEST_NONPROD_PUBLIC_KEY`). | Strict signature validation for schema >= 2.0.0; elimination of unsigned legacy 3-parameter digest fallback; untrusted public keys rejected. | `TESTED_AND_VERIFIED` |
| **ISO-PROV-01** | Provenance & Metadata Cross-Validation | Attestation Validator | Cross-comparison between top-level and nested provenance fields (`source_commit_sha`, `attestation_id`, `generated_at_utc`, `milestone`). | Mismatches between top-level and provenance metadata fail closed with `ContractViolationError`. | `TESTED_AND_VERIFIED` |
| **ISO-TIER-01** | Fail-Closed Runtime Profile Execution | Worker Isolation Client | Explicit runtime profile resolution (`docker_oci`, `wsl2_namespaces`, `native_namespaces`, `subprocess_fallback`). | Unavailable container/WSL2 engines raise `ContractViolationError`; zero silent fallbacks to subprocess execution. | `TESTED_AND_VERIFIED` |
| **ISO-MOUNT-01** | Container & WSL Checkpoint Path Resolution | Worker Isolation Client | Host checkpoint byte verification followed by container mount mapping (`/model/checkpoint.pt` for OCI, `/mnt/<drive>/...` for WSL2). | Container workers receive in-container mount path; host paths never leak into container IPC loading requests. | `TESTED_AND_VERIFIED` |
| **ISO-VAULT-01** | Multi-Route Private Vault Invisibility | In-Worker Isolation Probes | Traversal probing across direct paths, relative paths, symlinks, `/proc/1/root`, mount namespaces, env, and handles. | Target-specific probing under `/proc/1/root`; conclusive classification of `DENIED`, `NOT_FOUND`, `UNSUPPORTED`, `INCONCLUSIVE`. | `TESTED_AND_VERIFIED` |
| **ISO-QUAR-01** | Authoritative Recursive Holdout Quarantine | Quarantine Engine | Dynamic integration with `load_quarantine_record()` and recursive filesystem scan matching filenames and content SHA-256 digests. | Dynamic derivation of `quarantine_permanently_enforced`; detection of nested and renamed compromised holdout artifacts. | `TESTED_AND_VERIFIED` |
| **ISO-GOV-01** | Comprehensive Governance Invariant Verification | Schema Validator | Validation of all runtime invariants, policy declarations, quarantine registries, and production authorization states. | Any breach (`training_permitted != 0`, `new_holdout_generation != NOT_AUTHORIZED`, `final_evaluation != BLOCKED`) fails closed. | `TESTED_AND_VERIFIED` |
| **ISO-RES-01** | Cgroups v2 Resource Enforcement | Kernel Cgroups / Worker Probes | Enforcement of effective CPU cores (<= 2.0), memory (<= 2 GB), and PIDs (<= 128) via cgroups v2. | Distinguishes configured, observed, and enforced; rejects null/unavailable measurements from verified status. | `TESTED_AND_VERIFIED` |
| **ISO-NET-01** | Kernel Network Namespace Isolation | Kernel / OCI Container | Network namespace (`--network none`, `unshare -n`) and capability dropping (`--cap-drop=ALL`). | Requires actual recognized isolated tier (`docker_oci`, `native_namespaces`, `wsl2_namespaces`); rejects `subprocess_fallback`. | `TESTED_AND_VERIFIED` |
| **ISO-IPC-01** | Safe Binary IPC Protocol (HIPC) | Binary IPC Codec | Typed binary frames with length-prefixing, raw contiguous numpy tensor buffers, and zero pickle deserialization. | Corrupted magic (`b"BADM"`), raw pickle (`b"\x80\x04"`, `PK\x03\x04`), oversized headers (>1MB), and oversized payloads (>64MB) rejected. | `TESTED_AND_VERIFIED` |
| **ISO-REPLAY-01** | Nonce Tracking & Replay Attack Denial | IPC Client / Worker Filter | Independent per-request cryptographic nonce tracking on both evaluator and worker boundaries. | Duplicate request nonces rejected fail-closed on both evaluator client and worker server boundaries. | `TESTED_AND_VERIFIED` |

---

## Governance Certification
- `training_permitted = 0`
- `execution_authorized = 0`
- `gradient_steps = 0`
- `optimizer_updates = 0`
- `new_holdout_generation = NOT_AUTHORIZED`
- `production_keys = NOT_GENERATED`
- `production_vault = NOT_INITIALIZED`
- `final_evaluation = BLOCKED`
