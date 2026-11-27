# HOROS PROBE-1 — Stage 3.7C.3 Security Control Matrix

| Control ID | Control Name | Target Environment | Verification Status | Empirical Evidence / Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **ISO-UID-01** | Worker Identity & Privilege Escalation Restrictions | Linux OCI / Windows Subprocess | `TESTED_AND_VERIFIED` | In-worker active probing verified `setuid(0)` failure, `escalation_prevented=True`, and non-root execution (`WORKER_USER_SPEC="10001:10001"`). |
| **ISO-FS-01** | Read-Only Root Filesystem & Checkpoint Mount Writability Denial | Linux OCI / Windows Subprocess | `TESTED_AND_VERIFIED` | Active writability probes to `/`, `/app`, `/model/checkpoint.pt` failed closed with `EROFS`/`EACCES`/`PermissionError`. |
| **ISO-VAULT-01** | Private Vault Invisibility & Symlink / Procfs Traversal Denial | Host Filesystem / In-Worker | `TESTED_AND_VERIFIED` | Evaluator denies worker access to private vault directory; procfs inspection confirms `/proc/1/root` inaccessible and vault secrets unexposed. |
| **ISO-NET-01** | Kernel Network Namespace Isolation & Dropped Capabilities | Linux Namespaces / Container CI | `TESTED_AND_VERIFIED` | Outbound TCP connection to 8.8.8.8, DNS resolution, HTTP request, and raw socket creation actively probed and verified denied (`network_egress_denied=True`). |
| **ISO-RES-01** | Resource Limit Metrics & Quota Enforcement Schema | cgroups v2 / rlimit | `TESTED_AND_VERIFIED` | Active in-worker query verified retrieval of memory limits, PID limits, CPU quotas, and rlimits conforming to attestation schema. |
| **ISO-IPC-01** | Safe Binary Protocol (HIPC) & Malformed Frame Rejection | HIPC Channel | `TESTED_AND_VERIFIED` | Corrupted magic (`b"BADM"`), pickle magic (`b"\x80\x04"`, `PK\x03\x04`), truncated total lengths, negative lengths, and oversized frames (>64MB payload, >1MB header) rejected fail-closed. |
| **ISO-REPLAY-01**| Cryptographic Nonce Tracking & Replay Attack Denial | HIPC Channel | `TESTED_AND_VERIFIED` | Evaluator and worker enforce strict nonce consumption tracking; duplicate request nonces raise `ContractViolationError`. |
| **ISO-PROTO-01** | Tensor Finiteness & Schema Allowlisting | HIPC Serialization | `TESTED_AND_VERIFIED` | Floating-point tensors containing NaN or Inf, unsupported dtypes, and out-of-bounds offsets rejected fail-closed with `ContractViolationError`. |
| **ISO-GOV-01** | Research Governance Invariants | Codebase Global | `TESTED_AND_VERIFIED` | All 8 governance restrictions verified: `training_permitted=0`, `execution_authorized=0`, `gradient_steps=0`, `optimizer_updates=0`, `new_holdout_generation=NOT_AUTHORIZED`, `production_keys=NOT_GENERATED`, `production_vault=NOT_INITIALIZED`, `final_evaluation=BLOCKED`. |
| **ISO-ATTEST-01**| Cryptographic Security Attestation Integrity | Evaluator Provenance Engine | `TESTED_AND_VERIFIED` | Security attestation report generated with canonical SHA-256 evidence digest (`cf740ea6fcefe1ddbf4f4bbd0b5fc45d58126f9b084708c5aab371f2e793d547`); tamper detection verified. |

### Verification Status Legend:
- `TESTED_AND_VERIFIED`: Active empirical runtime test succeeded with reproducible evidence.
- `IMPLEMENTED`: Architectural mechanism implemented in codebase; verified via schema or configuration audit.
- `PARTIALLY_VERIFIED`: Verified under supported sub-features or platform-specific fallback modes.
- `BLOCKED`: Control blocked by external environment or platform restriction (none in Stage 3.7C.3).
