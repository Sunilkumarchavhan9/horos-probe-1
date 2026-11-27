# HOROS PROBE-1 — Stage 3.7C.4 Threat Model & Adversarial Analysis

## 1. System Overview & Scope
Stage 3.7C.4 implements and empirically validates cross-runtime security acceptance, resource enforcement, multi-route vault isolation, and cryptographic provenance anchoring for the isolated inference worker architecture within HOROS PROBE-1 (`Research-002`).

The primary security objective is to prevent an untrusted or adversarial candidate world model / inference worker from:
1. Exfiltrating holdout observations or privileged targets.
2. Compromising evaluator vault integrity or mutating ground-truth evaluations.
3. Accessing the network to establish covert exfiltration channels or C2 beacons.
4. Escalating privileges or breaking out of isolated OS/container boundaries.
5. Performing replay attacks, frame manipulation, or exploiting IPC deserialization vulnerabilities.
6. Exhausting CPU, memory, or process resources to cause denial-of-service (DoS) or disrupt evaluation fairness.
7. Forging or replaying security attestation documents without valid cryptographic anchoring.

---

## 2. Adversary Profile & Assumptions
- **Attacker Capabilities:**
  - Arbitrary code execution within the worker process context (simulated via candidate model weights or custom payloads).
  - Ability to craft arbitrary byte sequences across the IPC communication channel.
  - Ability to attempt raw syscalls (`setuid`, `socket`, `clone`, `fork`, `ptrace`, filesystem writes).
  - Ability to inspect `/proc`, `/sys`, environment variables, open file descriptors, and mount namespaces.
- **Attacker Constraints:**
  - Evaluator execution runs in a strictly higher-privilege security domain with exclusive access to the private vault and cryptographic keys.
  - Host kernel and hypervisor security boundaries are assumed uncompromised.
  - Production keys remain `NOT_GENERATED` and production vault remains `NOT_INITIALIZED`.

---

## 3. Threat Vectors & Mitigations

| Threat Vector | Attack Mechanism | Evaluator/Worker Mitigation | Validation Method |
| :--- | :--- | :--- | :--- |
| **TV-01: Deserialization Exploit** | Crafting malicious pickle payloads (`__reduce__` shellcode) or oversized frames across IPC. | Custom length-prefixed safe binary protocol (HIPC) with strict JSON metadata + raw contiguous array buffers; zero pickle usage; rejection of frames > 64MB or headers > 1MB. | Corrupted magic (`b"BADM"`), pickle magic (`b"\x80\x04"`, `PK\x03\x04`), oversized frames, and malformed frames rejected fail-closed. |
| **TV-02: Nonce Replay Attack** | Replaying past high-reward inputs or recorded IPC frames to bias evaluation scores. | Evaluator and worker enforce independent unique per-request nonce tracking; duplicate nonces trigger `ContractViolationError` and structured IPC error. | Test replaying identical request nonces asserts independent client-side and worker-side rejection. |
| **TV-03: Network Exfiltration** | Opening socket connections to external endpoints, DNS tunneling, or raw ICMP packets. | Dropping `CAP_NET_RAW` (via `--cap-drop=ALL` or unprivileged user namespace) prevents raw socket creation, while network namespace isolation (`--net=none`, `unshare -n`) controls network reachability (TCP, UDP, DNS, ICMP). On standard subprocess fallback tiers, `HTTP_PROXY`/`ALL_PROXY` blackholing provides library-level defense-in-depth, but raw socket and kernel network stack restrictions require containerized or namespace isolation. | Active socket diagnostics test external TCP (8.8.8.8), DNS, HTTP, and raw sockets; fail closed under container/namespace isolated tiers. |
| **TV-04: Multi-Route Vault Traversal** | Access via direct paths, relative traversal (`../../`), procfs symlinks (`/proc/self/cwd`), `/proc/1/root`, mount namespace leaks, or handle inheritance. | Worker runs with zero vault mounts, deny ACLs, stripped environment, closed handles, and isolated mount namespace. Each route is conclusively classified: `DENIED`, `NOT_FOUND`, `UNSUPPORTED`, or `INCONCLUSIVE`. | Multi-route traversal probing across direct, relative, symlink, procfs, mountinfo, env, and fd interfaces verifies vault invisibility. |
| **TV-05: Filesystem Tampering** | Writing to root filesystem, mutating checkpoint weights, or injecting persistent backdoors. | Read-only root filesystem mount (`--read-only`) and read-only checkpoint volume mount (`ro`). In subprocess fallback, root writability probe verifies denial while checkpoint mount isolation is reported as partially verified. | In-worker write probes to root, system, and checkpoint paths verify `EROFS`/`EACCES`/`PermissionError`. |
| **TV-06: Privilege Escalation** | Invoking `setuid(0)` or exploiting SUID binaries. | Non-root UID/GID (`10001:10001`), `NoNewPrivileges=true`, and all capabilities dropped (`--cap-drop=ALL`). Token non-admin status on Windows reported as token membership only. | In-worker `os.setuid(0)` invocation explicitly caught and verified prevented on POSIX; token membership verified on Windows. |
| **TV-07: Resource Exhaustion (DoS)** | Fork bombs, memory leaks, or CPU starvation to disrupt evaluation. | Cgroups v2 resource limits (CPU: 2.0 cores, Memory: 2 GB, PIDs: 128). Distinguishes configured, observed, and enforced limits; never reports null or unavailable measurements as verified. | In-worker query reads effective cgroups v2 quota from `/proc/self/cgroup` and `/sys/fs/cgroup`. |
| **TV-08: Attestation Forgery / Tampering** | Mutating control records, candidate checkpoint hashes, or governance invariants in attestation reports. | Canonical JSON serialization hashed with SHA-256 and cryptographically signed via Ed25519 using a dedicated non-production test key (`TEST_NONPROD_PUBLIC_KEY`). Any mutation breaks signature verification. | Tamper detection tests assert that mutating any control, runtime identity, or governance invariant invalidates the signature and digest. |

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
