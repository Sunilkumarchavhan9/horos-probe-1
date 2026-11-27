# HOROS PROBE-1 — Stage 3.7C.3 Threat Model & Adversarial Analysis

## 1. System Overview & Scope
Stage 3.7C.3 implements and validates active runtime enforcement and empirical security attestation for the isolated inference worker architecture within HOROS PROBE-1 (`Research-002`).

The primary security objective is to prevent an untrusted or adversarial candidate world model / inference worker from:
1. Exfiltrating holdout observations or privileged targets.
2. Compromising evaluator vault integrity or mutating ground-truth evaluations.
3. Accessing the network to establish covert exfiltration channels or C2 beacons.
4. Escalating privileges or breaking out of isolated OS/container boundaries.
5. Performing replay attacks or exploiting IPC deserialization vulnerabilities.

---

## 2. Adversary Profile & Assumptions
- **Attacker Capabilities:**
  - Full arbitrary code execution inside the worker process (simulated via candidate model weights or custom payloads).
  - Ability to craft arbitrary byte sequences across the IPC communication channel.
  - Ability to attempt raw syscalls (`setuid`, `socket`, `ptrace`, filesystem modifications).
  - Ability to inspect `/proc`, `/sys`, environment variables, and memory mappings accessible to the worker token/UID.
- **Attacker Constraints:**
  - Evaluator execution runs in a higher-privilege security domain with exclusive access to the private vault and cryptographic keys.
  - Host kernel and hypervisor security boundaries are assumed uncompromised.

---

## 3. Threat Vectors & Mitigations

| Threat Vector | Attack Mechanism | Evaluator/Worker Mitigation | Validation Method |
| :--- | :--- | :--- | :--- |
| **TV-01: Deserialization Exploit** | Crafting malicious pickle payloads (`__reduce__` shellcode) across IPC. | Custom length-prefixed safe binary protocol (HIPC) with strict JSON metadata + raw contiguous array buffers; zero pickle usage. | Corrupted magic (`b"BADM"`), pickle magic (`b"\x80\x04"`, `PK\x03\x04`), and malformed frames rejected fail-closed. |
| **TV-02: Nonce Replay Attack** | Replaying past high-reward inputs or recorded IPC frames to bias evaluation scores. | Evaluator and worker enforce unique per-request nonce tracking; duplicate nonces trigger `ContractViolationError`. | Test replaying identical request nonces asserts rejection. |
| **TV-03: Network Exfiltration** | Opening socket connections to external endpoints, DNS tunneling, or raw ICMP packets. | Dropping `CAP_NET_RAW` (via `--cap-drop=ALL` or unprivileged user namespace) prevents raw socket creation, while network namespace isolation (`--net=none`, `unshare -n`) controls network reachability (TCP, UDP, DNS, ICMP). On standard subprocess fallback tiers, `HTTP_PROXY`/`ALL_PROXY` blackholing provides library-level defense-in-depth, but raw socket and kernel network stack restrictions require containerized or namespace isolation. | Active socket diagnostics test external TCP (8.8.8.8), DNS, HTTP, and raw sockets; fail closed under container/namespace isolated tiers. |
| **TV-04: Vault Symlink / Traversal** | Traversal via `../`, procfs symlinks (`/proc/1/root`), or environment inspection. | Worker runs with no access or deny ACLs to vault paths; `/proc/1/root` inaccessible to unprivileged container/user namespace. | In-worker traversal probing across 6 candidate traversal vectors verifies vault invisibility. |
| **TV-05: Filesystem Tampering** | Writing to root filesystem, mutating checkpoint weights, or injecting persistent backdoors. | Read-only root filesystem mount (`--read-only`) and read-only checkpoint volume mount (`ro`). | In-worker writability probes to root, system, and checkpoint paths verify `EROFS`/`EACCES`/`PermissionError`. |
| **TV-06: Privilege Escalation** | Invoking `setuid(0)` or exploiting SUID binaries. | Non-root UID/GID (`10001:10001`), `NoNewPrivileges=true`, and all capabilities dropped (`--cap-drop=ALL`). | In-worker `os.setuid(0)` invocation explicitly caught and verified prevented. |
| **TV-07: Resource Exhaustion (DoS)** | Fork bombs, memory leaks, or CPU starvation to disrupt evaluation. | Cgroups v2 resource limits (CPU quotas, memory caps, PID limits). | In-worker query verifies resource limit schema and cgroup thresholds. |

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
compromised_holdout_id = stage35_final_holdout
compromised_holdout_status = COMPROMISED_PUBLIC_EXPOSURE
compromised_holdout_protocol = HOROS-P1-R002-STAGE3.5-FINAL_HOLDOUT
```
