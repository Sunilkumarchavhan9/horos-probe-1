# HOROS PROBE-1 — Stage 3.7C.6.1 Threat Model
## Post-Merge Trust Verification & Boundary Hardening

### 1. Scope & Objective
This threat model analyzes the trust boundary between the privileged evaluator, the untrusted worker isolation runtime, the cryptographic attestation engine, and historical artifact quarantine storage. The objective is to eliminate fail-open assumptions, ensure unreachable code paths cannot mask verification failures, and prevent snapshot observations from being conflated with permanent governance controls.

---

### 2. Threat Actors & Attack Vectors

| Actor ID | Actor Description | Access Level | Motive / Objective |
| :--- | :--- | :--- | :--- |
| **ADV-01** | Compromised Inference Worker | Subprocess or Container Jail | Exfiltrate private holdout seeds, manipulate IPC frames, escape filesystem mounts. |
| **ADV-02** | Malicious / Misconfigured Pipeline | Evaluator Host Execution | Inject uncommitted / dirty source revisions, forge attestation records, downgrade schema version. |
| **ADV-03** | Corrupted / Tampered Attestation | Machine-Readable JSON Record | Substitute public keys, strip Ed25519 signatures, replace evidence digests. |
| **ADV-04** | Accidental / Covert Holdout Exposure | Filesystem Artifact Leaks | Re-introduce quarantined Stage 3.5 holdout data through copied directories or renamed files. |

---

### 3. Threat Analysis & Mitigations

#### Threat T1: Dead-Code Bypass in Production Attestation Acceptance
- **Vulnerability:** Lines 496–505 of `security_attestation.py` were indented inside an `if` block that unconditionally raised an exception, leaving signature verification unreachable and causing valid records to return `None`.
- **Impact:** Production acceptance could never return `True` for valid records, preventing legitimate deployment or falsely relying on unreached checks.
- **Mitigation (P59.1):** Un-indented signature verification to the top-level scope. Ensured that `verify_production_attestation_acceptance()` strictly returns `True` only after Ed25519 signature verification against the recomputed canonical payload digest succeeds. Any validation failure raises an explicit `ContractViolationError`. Tested via ephemeral in-memory Ed25519 key fixtures (`test_p59_01`, `test_p59_02`).

#### Threat T2: Silent Git Failure Producing False Clean Tree Provenance
- **Vulnerability:** `is_source_tree_dirty()` caught all exceptions from `git status` and returned `False`, falsely certifying unverified source trees as clean when Git was unavailable, broken, or timed out.
- **Impact:** An untrusted, dirty, or corrupted source tree could be accepted as verified clean provenance.
- **Mitigation (P59.2):** `is_source_tree_dirty()` now fails closed by raising `ContractViolationError` on non-zero exit codes, command timeouts, or missing `.git` metadata. In `SecurityAttestationBuilder`, failures set `is_dirty_source_tree = True`. Production acceptance strictly rejects dirty or unverified source trees (`test_p59_05`, `test_p59_06`, `test_p59_07`, `test_p59_08`).

#### Threat T3: Base Commit Provenance Substitution for Newer Uncommitted Code
- **Vulnerability:** Claiming a valid base commit SHA while running modified or uncommitted code in the working tree.
- **Impact:** Attestation record falsely binds to a commit that does not contain the actual executed code.
- **Mitigation (P59.2):** Hashing complete recursive Python source files with `compute_source_tree_digest()`, validating commit SHA with `[0-9a-f]{40}` regex, and verifying commit existence in the repository object database via `git cat-file -e <sha>^{commit}` (`test_p59_09`, `test_p59_10`).

#### Threat T4: Filesystem Snapshot Conflated with Permanent Enforcement
- **Vulnerability:** Setting `quarantine_permanently_enforced = physical_exclusion_verified` directly from a single clean directory scan.
- **Impact:** A clean temporary scan is cited as evidence of permanent architectural holdout exclusion, ignoring whether permanent governance controls exist.
- **Mitigation (P59.3):** Separated `scan_complete`, `physical_exclusion_verified`, `quarantine_enforcement_controls_verified`, and `quarantine_permanently_enforced`. In snapshot scans (custom `search_dirs`), permanent enforcement requires explicit verified enforcement evidence (`test_p59_16`, `test_p59_17`, `test_p59_18`).

#### Threat T5: Broad Directory Exclusion Hiding Compromised Artifacts
- **Vulnerability:** Skipping the entire canonical `stage35_production` directory or any file named `holdout_quarantine_record.json`.
- **Impact:** An attacker could place compromised holdout files in `stage35_production/prelift_long` or create copied registry files elsewhere without detection.
- **Mitigation (P59.3):** Narrowed exemptions strictly to `CANONICAL_QUARANTINE_REGISTRY_PATH`, `stage36_holdout_quarantine`, `stage35_production/final_holdout`, and canonical manifests. Any other file named `holdout_quarantine_record.json` or placed in `stage35_production` outside `final_holdout` is scanned and flagged (`test_p59_14`, `test_p59_15`).

#### Threat T6: Silent Directory Traversal Failures
- **Vulnerability:** `os.walk` without `onerror` silently skips inaccessible directories, falsely reporting `scan_complete = True`.
- **Impact:** Unscanned directories could harbor exposed holdout files.
- **Mitigation (P59.3):** Integrated `onerror=on_walk_error` callback to capture traversal errors into `unreadable_files` and set `scan_complete = False` (`test_p59_11`, `test_p59_12`).

#### Threat T7: Runtime Capability Substitution
- **Vulnerability:** Reporting container isolation based on host feature availability rather than actual worker runtime execution.
- **Impact:** Subprocess fallback worker falsely claims container or namespace isolation.
- **Mitigation (P59.4, P59.5):** Required strict matching between control evidence tier and `active_runtime_tier`. Subprocess fallback is accurately reported without container promotion (`test_p59_19`).
