# HOROS PROBE-1 — Stage 3.7C.6.1 Security Control Matrix
## Attestation Trust & Quarantine Enforcement Controls

| Control ID | Control Name | Threat Addressed | Enforcement Mechanism | Verification Test | Status |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **ISO-UID-01** | Worker Identity & Privilege Escalation Restrictions | ADV-01 (Privilege Escalation) | Prohibits root/admin tokens; enforces non-elevated user token; rejects escalation override via `no_new_privs` alone. | `test_p58_15`, `test_04` (P56) | **VERIFIED** |
| **ISO-FS-01** | Read-Only Root & Checkpoint Mount Writability Denial | ADV-01 (Checkpoint Tampering) | Read-only mounts in container tier; read-only verification probes across filesystem targets. | `test_p58_21`, `test_14` (P56) | **VERIFIED** |
| **ISO-VAULT-01** | Private Vault Invisibility & Symlink / Procfs Denial | ADV-01 (Secret Vault Exfiltration) | ACL denial, symlink rejection, `/proc/1/root` traversal verification across multiple access routes. | `test_p58_11`, `test_11` (P57) | **VERIFIED** |
| **ISO-NET-01** | Kernel Network Namespace Isolation | ADV-01 (Network Exfiltration) | Network namespace isolation, CAP_NET_RAW dropping, proxy blackholing. Requires container tier for tested status. | `test_p58_14`, `test_05` (P56) | **PARTIALLY VERIFIED** (Subprocess) |
| **ISO-RES-01** | Resource Quota & Limit Enforcement Schema | ADV-01 (Resource Exhaustion / DoS) | Cgroups v2 CPU quota (2 cores), memory.max (2 GB), pids.max (128). Rejects null observations for tested status. | `test_p58_16`, `test_p59_19` | **PARTIALLY VERIFIED** (Subprocess) |
| **ISO-IPC-01** | Safe Binary IPC Protocol (HIPC) Hardening | ADV-01, ADV-02 (Protocol Exploitation) | Binary length-prefixed protocol, magic byte validation (`HIPC`), pickle deserialization rejection. | `test_12` (P56), `test_06` (P56) | **VERIFIED** |
| **ISO-REPLAY-01** | Cryptographic Nonce Tracking & Replay Attack Denial | ADV-01, ADV-02 (Replay Attack) | Evaluator-side and worker-side independent nonce tracking; immediate fail-closed on duplicate nonces. | `test_p57_12`, `test_14` (P56) | **VERIFIED** |
| **ISO-GOV-01** | Non-Negotiable Governance Invariants | ADV-02 (Governance Bypass) | Numeric lock on `training_permitted = 0`, `execution_authorized = 0`; static policy checks. | `test_p58_17`, `test_07` (P56) | **VERIFIED** |
| **ISO-PROV-01** | Fail-Closed Source Provenance & Commit Object Verification | ADV-02 (Untrusted Source Injection) | Fail-closed `is_source_tree_dirty()`, deterministic recursive source tree hashing, `git cat-file -e` commit verification. | `test_p59_05` through `test_p59_10` | **VERIFIED** |
| **ISO-SIGN-01** | Cryptographic Key Separation & Production Acceptance | ADV-03 (Signature Forgery / Key Substitution) | Ephemeral test key isolation; `verify_production_attestation_acceptance()` strictly returns True on valid Ed25519 signature. | `test_p59_01` through `test_p59_04` | **VERIFIED** |
| **ISO-QUAR-01** | Recursive Quarantine Exclusion & Concept Separation | ADV-04 (Holdout Exposure) | Decoupled `scan_complete`, `physical_exclusion_verified`, `quarantine_permanently_enforced`; `onerror` error capture; narrow exemptions. | `test_p59_11` through `test_p59_18` | **VERIFIED** |

---

### Invariant Status Summary

| Governance Invariant | Enforced Value | Verification Status |
| :--- | :---: | :---: |
| `training_permitted` | `0` | Strictly Enforced (Numeric 0) |
| `execution_authorized` | `0` | Strictly Enforced (Numeric 0) |
| `gradient_steps` | `0` | Strictly Enforced (Numeric 0) |
| `optimizer_updates` | `0` | Strictly Enforced (Numeric 0) |
| `new_holdout_generation` | `NOT_AUTHORIZED` | Static Policy Declaration Locked |
| `production_keys` | `NOT_GENERATED` | Ephemeral Test Fixture Only |
| `production_vault` | `NOT_INITIALIZED` | Synthetically Isolated Vaults Only |
| `final_evaluation` | `BLOCKED` | Evaluation Execution Blocked |
| `stage35_holdout` | `PERMANENTLY_COMPROMISED` | Authoritatively Quarantined |
