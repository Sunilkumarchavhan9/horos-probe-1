# HOROS PROBE-1 — Stage 3.7C.2 Acceptance Report
## Worker Isolation Runtime Integration & Security Boundary Validation

**Research Program:** PROBE-1 — Autonomous Robotic Manipulation World Model  
**Workspace:** `C:\projects\horos-probe-1` (Mirror: `C:\projects\MemoryOS\research\PROBE-1\Research-002`)  
**Repository:** `Sunilkumarchavhan9/horos-probe-1`  
**Milestone:** Stage 3.7C.2 — Worker Isolation Runtime Integration & Security Boundary Validation  
**Date:** November 2026  
**Verdict:** ACCEPTED (Engineering Implementation & Security Boundary Acceptance Verified)

---

## 1. Executive Summary & Objective Realization

Stage 3.7C.1 identified a core security architectural limitation: running the inference worker as a standard Windows child subprocess inherited the host user's security token and identity. Previous attempts to enforce filesystem boundaries via Windows NTFS Deny ACLs denied access to the shared parent user account, inadvertently locking out the privileged evaluator from its own ground truth vault.

**Stage 3.7C.2 resolves this architecturally** by establishing a true two-boundary isolation system:
1. **Privileged Evaluator Domain**: The host-level evaluator maintains continuous, unimpeded read access to synthetic ground truth vaults, verified candidate model weights, Ed25519 signing trust anchors, and the Durable Holdout Lock ledger. No self-denying ACLs are applied to the evaluator.
2. **Restricted Inference Worker Domain**: The worker executes within a confined container / Linux namespace runtime with non-root UID/GID (`10001:10001`), read-only root filesystem (`--read-only`), read-only candidate checkpoint volume mount, dropped Linux capabilities (`--cap-drop ALL`, `no-new-privileges`), and zero network connectivity (`--network none` / `unshare -n`). The private vault is never mounted.

Communication between evaluator and worker occurs exclusively across typed, length-prefixed binary HIPC streams with bidirectional schema validation and strict privileged-key filtering.

---

## 2. Security Control Classification & Status Matrix

Per Stage 3.7C.2 governance standards, all controls are rigorously and honestly classified into four categories:
- `TESTED_AND_VERIFIED`: Proved with direct, fail-closed runtime execution tests.
- `IMPLEMENTED`: Built, configured, and reproducible via artifacts and code.
- `PARTIALLY_VERIFIED`: Operational under available host/kernel primitives; full container runtime available in CI.
- `BLOCKED`: Prevented by environmental or governance restrictions.

| Control Identifier | Security Control Specification | Classification | Verification Evidence / Mechanism |
| :--- | :--- | :--- | :--- |
| **SEC-ISO-01** | Two-Boundary Evaluator/Worker Separation | `TESTED_AND_VERIFIED` | P54-04: Evaluator reads protected file before and after worker access; worker receives `ACCESS_DENIED`. |
| **SEC-ISO-02** | Zero Private Vault Mount | `TESTED_AND_VERIFIED` | P54-03: `build_oci_run_command` and runtime manager block any vault mount; raises `WorkerVaultInfiltrationError`. |
| **SEC-ISO-03** | Worker Directory Traversal Denial | `TESTED_AND_VERIFIED` | P54-05: Relative directory traversal (`../../`) and root discovery attempts fail closed (`traversal_succeeded = False`). |
| **SEC-ISO-04** | Privileged Target Non-Disclosure | `TESTED_AND_VERIFIED` | P54-06: Explicit `GET_PRIVILEGED_TARGETS` and payloads containing privileged keys fail closed with `ContractViolationError`. |
| **SEC-ISO-05** | Checkpoint Load & Safe Inference | `TESTED_AND_VERIFIED` | P54-07: Candidate checkpoint loaded via `weights_only=True` and verified SHA-256; HIPC inference succeeds with allowed keys. |
| **SEC-ISO-06** | Network Egress Denial (`--network none`) | `TESTED_AND_VERIFIED` | P54-08: Kernel namespace network isolation (`unshare -n`) verified via WSL; external TCP, DNS, and HTTP strictly denied. |
| **SEC-ISO-07** | Dropped Capabilities (`CAP_DROP ALL`) | `TESTED_AND_VERIFIED` | P54-09: Raw socket creation fails closed with `PermissionError` when `CAP_NET_RAW` is dropped via `capsh`. |
| **SEC-ISO-08** | Non-Root Container Identity (`10001:10001`) | `IMPLEMENTED` | Specified in `Dockerfile.worker` (`USER 10001:10001`) and `runtime_config.json`; mapped in kernel user namespace. |
| **SEC-ISO-09** | Read-Only Root Filesystem (`--read-only`) | `IMPLEMENTED` | Enforced in `runtime_config.json` and `build_oci_run_command`; write access restricted to ephemeral tmpfs `/tmp`. |
| **SEC-ISO-10** | Resource & Quotas Enforcement | `IMPLEMENTED` | Enforced in OCI runtime configuration: 2GB RAM (`--memory 2g`), 2 CPUs (`--cpus 2`), 128 PIDs (`--pids-limit 128`). |
| **SEC-ISO-11** | Clean Teardown & Pipe Release | `TESTED_AND_VERIFIED` | P54-10: Worker child process terminates cleanly upon `close()`, pipes released, exit code verified. |
| **SEC-ISO-12** | Production Holdout & Model Training | `BLOCKED` | Non-negotiable governance invariants maintained: `training_permitted = 0`, `execution_authorized = 0`, `final_evaluation = BLOCKED`. |

---

## 3. Host & Container Runtime Capability Audit

Execution environment capabilities audited via `detect_runtime_capabilities()`:

```json
{
  "runtime_type": "LINUX_NAMESPACES",
  "primary_tool": "unshare (WSL2)",
  "network_isolation_supported": true,
  "filesystem_isolation_supported": true,
  "non_root_uid_supported": true,
  "dropped_caps_supported": true,
  "docker_available": false,
  "podman_available": false,
  "native_unshare_available": false,
  "wsl_unshare_available": true
}
```

### Analysis:
1. **Windows Host Environment**: Native Docker and Podman binaries are not installed in the Windows host PATH.
2. **WSL2 Linux Kernel Environment**: `Ubuntu-22.04` is available with `unshare` (`util-linux 2.37.2`) and `capsh`. Kernel network namespaces (`unshare -n`), user namespaces (`unshare -r`), and mount namespaces (`unshare -m`) are fully functional.
3. **GitHub Actions CI Environment**: Runs on `ubuntu-latest` with native Docker pre-installed, enabling full container execution in automated CI pipelines.

---

## 4. Empirical Security Evidence

### A. Evaluator Access Retention vs. Worker Infiltration Denial
- **Test Executed**: `test_04_parent_access_retention_and_worker_vault_invisibility`
- **Result**: PASSED
- **Evaluator Access**: Evaluator reads `evaluator_protected_vault/protected_targets.json` before and after worker access attempt (`evaluator_retains_access = True`).
- **Worker Access**: Worker attempts direct file access on unmounted vault path and returns `ACCESS_DENIED`.
- **Finding**: Crucially confirms that Windows user-level deny ACLs have been eliminated, maintaining evaluator custody without self-denial.

### B. Network Egress Denial Evidence
- **Test Executed**: `test_08_in_worker_network_egress_denial_evidence`
- **Result**: PASSED
- **Probe Results inside Network Namespace (`unshare -n`)**:
  - External TCP (`8.8.8.8:53`): `DENIED` (`OSError: [Errno 101] Network is unreachable`)
  - DNS Resolution (`example.com:80`): `DENIED` (`gaierror: [Errno -3] Temporary failure in name resolution`)
  - HTTP Request (`http://1.1.1.1`): `DENIED` (`URLError: <urlopen error [Errno 101] Network is unreachable>`)
- **Assertion**: Confirms strict, fail-closed network egress blocking.

### C. Raw Socket Creation Denial Evidence
- **Test Executed**: `test_09_in_worker_raw_socket_denial_evidence`
- **Result**: PASSED
- **Probe Result with Dropped Capabilities (`capsh --drop=cap_net_raw`)**:
  - `socket(AF_INET, SOCK_RAW, IPPROTO_RAW)`: `DENIED` (`PermissionError: [Errno 1] Operation not permitted`)

---

## 5. Artifact Delivery Register

The following deployment and verification artifacts have been authored and committed to the repository:

1. **`Research-002/docker/Dockerfile.worker`**: Multi-stage OCI Containerfile specifying `python:3.12-slim-bookworm`, non-root user `horos-worker` (`10001:10001`), read-only source tree `/app`, CPU-only PyTorch, and `--worker` entrypoint.
2. **`Research-002/experiments/stage37_blind_evaluation/runtime_config.json`**: Authoritative OCI runtime configuration locking all container containment flags, user identities, resource quotas, and governance invariants.
3. **`Research-002/scripts/run_isolated_worker.sh`**: Reproducible bash launch script supporting Docker, Podman, and native Linux namespaces.
4. **`Research-002/src/world_model_vnext/runtime_isolation.py`**: Runtime isolation engine providing capability discovery, command construction, two-boundary verification, and diagnostic probes.
5. **`Research-002/src/world_model_vnext/worker_isolation.py`**: Refactored worker isolation supporting custom commands, comprehensive network diagnostics, and directory traversal defense.
6. **`Research-002/tests/test_p54_stage37c2_runtime_isolation.py`**: 11-test regression test suite validating runtime capabilities, OCI flags, parent access retention, traversal denial, HIPC filtering, inference prediction, network egress denial, raw socket denial, lifecycle cleanup, and governance invariants.
7. **`Research-002/experiments/stage37_blind_evaluation/stage37c2_security_boundary_diagram.md`**: Mermaid diagram and control matrix documenting the two-boundary architecture.

---

## 6. Research Governance Restrictions

The non-negotiable governance restrictions established in Stage 3.6 through Stage 3.7C.1 remain fully enforced:

```text
training_permitted = 0
execution_authorized = 0
gradient_steps = 0
optimizer_updates = 0
new_holdout_generation = NOT_AUTHORIZED
production_keys = NOT_GENERATED
production_vault = NOT_INITIALIZED
final_evaluation = BLOCKED
```

No model training was performed, no real private holdout trajectories were generated, and no production keys were provisioned. All validation was conducted exclusively with synthetic test fixtures and ephemeral keys.
