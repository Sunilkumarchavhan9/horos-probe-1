# HOROS PROBE-1 — Stage 3.7C.2: Security Boundary Architecture Diagram

Research Program: PROBE-1 — Autonomous Robotic Manipulation World Model  
Repository: `Sunilkumarchavhan9/horos-probe-1`  
Milestone: Stage 3.7C.2 — Worker Isolation Runtime Integration & Security Boundary Validation

---

## 1. Executive Summary

Stage 3.7C.2 addresses the principal-separation limitation identified in Stage 3.7C.1 by implementing a genuine two-boundary execution architecture:
- **Boundary 1: Privileged Evaluator (Host Domain)**
- **Boundary 2: Restricted Inference Worker (Container / Kernel Namespace Domain)**

Crucially, the privileged evaluator retains continuous read access to protected synthetic test data in its private vault throughout the evaluation lifecycle, while the restricted inference worker demonstrably cannot discover, traverse to, or read the vault file, nor execute outbound network calls.

---

## 2. Architecture & Security Boundary Topology

```mermaid
flowchart TD
    subgraph PRIVILEGED_DOMAIN["Privileged Evaluator Boundary (Host OS / Evaluator Context)"]
        direction TB
        VAULT[("Protected Private Vault\n(holdout_weights_secret, targets)")]
        EVALUATOR["HCTTReadOnlyEvaluator / Test Harness"]
        KEYS["Ed25519 Signing Keys &\nLedger Auth Secret"]
        LEDGER[("Durable Holdout Lock Ledger\n(HMAC-SHA256 Authenticated)")]
        CHECKPOINT[("Candidate Checkpoint\n(SHA-256 Verified)")]

        EVALUATOR -->|Direct Read Access| VAULT
        EVALUATOR -->|Verify Manifest| KEYS
        EVALUATOR -->|Reserve / Record| LEDGER
        EVALUATOR -->|Verify SHA-256| CHECKPOINT
    end

    subgraph HIPC_CHANNEL["Typed Binary IPC Channel (HIPC_V1 Wire Protocol)"]
        direction LR
        PIPE_IN["Evaluator Stdin Stream\n[MAGIC: HIPC | Len | JSON Header | Float32/Int64 Tensors]"]
        FILTER["Bidirectional IPC Filter\n(Blocks Privileged Keys / NaN / Inf)"]
        PIPE_OUT["Evaluator Stdout Stream\n[Allowlisted Predictions Only]"]
        
        PIPE_IN --> FILTER --> PIPE_OUT
    end

    subgraph WORKER_DOMAIN["Restricted Inference Worker Boundary (Container / Namespace Sandbox)"]
        direction TB
        CONTAINER["Worker Execution Sandbox\n- UID/GID: 10001:10001 (Non-root)\n- Network: --network none\n- Root FS: --read-only\n- Capabilities: --cap-drop ALL\n- Security Opt: no-new-privileges\n- Memory: 2GB | CPU: 2.0 | PIDs: 128"]
        
        MODEL_PROCESS["world_model_vnext.worker_isolation\n(HCTTWorldModel eval mode, torch.no_grad)"]
        
        MOUNT_CKPT[("/model/checkpoint.pt\n(Read-Only Volume Mount)")]
        TMPFS[("/tmp:rw,noexec,nosuid,size=64m\n(Ephemeral Scratch)")]
        
        CONTAINER --- MODEL_PROCESS
        CONTAINER --- MOUNT_CKPT
        CONTAINER --- TMPFS
    end

    EVALUATOR ==>|Transmit Filtered Inputs| PIPE_IN
    PIPE_OUT ==>|Return Allowed Predictions| EVALUATOR

    MODEL_PROCESS -->|Strict weights_only Load| MOUNT_CKPT

    VAULT -.-x|NOT MOUNTED\nAccess Denied / Not Found| CONTAINER
    KEYS -.-x|NO ACCESS| CONTAINER
    LEDGER -.-x|NO ACCESS| CONTAINER

    CONTAINER -.-x|Network Egress Denied\n(TCP / DNS / HTTP / Raw)| INTERNET((Outbound Network / Internet))
```

---

## 3. Boundary Comparison & Control Matrix

| Security Dimension | Privileged Evaluator Domain | Restricted Inference Worker Domain | Enforcement Mechanism |
| :--- | :--- | :--- | :--- |
| **Principal Identity** | Host User / CI Runner | UID `10001` / GID `10001` (`horos-worker`) | Container / User Namespace UID mapping |
| **Private Vault Access** | Full Read/Write Access | **Zero Access** (Directory completely unmounted) | Volume Mount Isolation (No `-v` for vault) |
| **Network Egress** | Host / CI Network | **Blocked** (`--network none` / `unshare -n`) | Kernel Network Namespace isolation |
| **Raw Socket Creation** | Allowed (Host Policy) | **Denied** (`Operation not permitted`) | Dropped Linux capabilities (`CAP_DROP ALL`) |
| **Filesystem Mutation** | Workspace Read/Write | **Read-Only Root** (`--read-only`) | Kernel VFS read-only mount enforcement |
| **Temporary Storage** | Host Temp | Restricted tmpfs (`noexec, nosuid, size=64m`) | Mount options `noexec`, `nosuid` |
| **Candidate Weights** | Full Artifact Custody | Read-Only Mount (`/model/checkpoint.pt:ro`) | OCI Read-Only bind mount (`:ro`) |
| **IPC Data Transport** | Host Controller Endpoint | Subprocess Stdin/Stdout Pipes | Length-prefixed HIPC typed binary stream |
| **Privileged Keys IPC** | Manages Targets & Seeds | **Strictly Forbidden** (Raises ContractViolation) | Bidirectional regex and schema validation |
| **Execution Limits** | Host-determined | 2 CPUs, 2GB RAM, 128 PIDs | cgroups v2 resource quotas |

---

## 4. Verification Evidence Summary

1. **Parent Access Retention**: The evaluator writes and reads `evaluator_protected_vault/protected_targets.json`. Parent retains read access throughout worker execution.
2. **Worker Vault Invisibility**: The worker receives no volume mount for the vault. Direct path and traversal attempts (`../../`) fail closed with `ACCESS_DENIED`, `ERROR`, or `NOT_FOUND`.
3. **Network Egress Denial**: In-worker diagnostics verify that external TCP (`8.8.8.8:53`), DNS resolution (`example.com`), and HTTP requests fail closed with `OSError: Network is unreachable`, `gaierror`, and `URLError`. Raw sockets fail with `PermissionError`.
4. **Candidate Model Inference**: Worker loads verified candidate weights (`weights_only=True`, SHA-256 verified) and executes inference over HIPC returning allowlisted predictions.
