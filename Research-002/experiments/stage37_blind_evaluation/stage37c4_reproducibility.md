# HOROS PROBE-1 — Stage 3.7C.4 Reproducibility Guide

## 1. Overview
This document specifies exact reproduction instructions for the Stage 3.7C.4 cross-runtime security acceptance, resource enforcement, multi-route vault isolation, and cryptographic provenance anchoring framework.

---

## 2. Environment Setup

### Prerequisites
- Python 3.12 (standard CPython)
- Virtual environment or package dependencies:
  ```bash
  pip install pytest numpy torch scipy==1.14.1 cryptography
  ```

### Workspaces
- Primary Repository: `C:\projects\horos-probe-1` (or cloned repository root)
- Module Path: Set `PYTHONPATH="Research-002:Research-002/src"` (or `src` from `Research-002/`)

---

## 3. Execution Profiles
`IsolatedInferenceWorker` supports four distinct execution profiles via the `execution_profile` parameter:
1. `PROFILE_SUBPROCESS_FALLBACK` (`"subprocess_fallback"`):
   Runs as a direct child Python process with stripped environment, proxy blackholing, and strict token audit.
2. `PROFILE_NATIVE_NAMESPACES` (`"native_namespaces"`):
   Runs using `unshare -n -r -m -u -p -f --mount-proc` on native Linux.
3. `PROFILE_WSL2_NAMESPACES` (`"wsl2_namespaces"`):
   Runs unshare namespaces through Windows Subsystem for Linux (WSL2).
4. `PROFILE_DOCKER_OCI` (`"docker_oci"`):
   Runs inside an OCI container (`docker run --network none --read-only --user 10001:10001 --cap-drop ALL ...`).
5. `PROFILE_AUTO` (`"auto"`):
   Resolves to native Linux namespaces when running on native POSIX Linux; defaults to subprocess fallback on Windows host. Never infers namespace isolation from host capability availability.

---

## 4. Running Attestation Generation

To run active in-worker empirical probes and generate the signed machine-readable attestation report:
```bash
python Research-002/scripts/generate_attestation_report.py
```

Expected Output:
- Saved verified security attestation report to `Research-002/experiments/stage37_blind_evaluation/security_attestation_report.json`
- Canonical Evidence Digest (SHA-256): 64-hex string
- Attestation Signature (Ed25519): 128-hex signature verified against non-production test public key.

---

## 5. Running Test Regressions

### Running P56 Cross-Runtime Acceptance Suite
```bash
python -m pytest Research-002/tests/test_p56_stage37c4_cross_runtime_acceptance.py -v
```
Expected: 14 passed in ~12 seconds.

### Running Complete Stage 3.7 Regression Suite (P50–P56)
```bash
python -m pytest Research-002/tests/test_p50* Research-002/tests/test_p51* Research-002/tests/test_p52* Research-002/tests/test_p53* Research-002/tests/test_p54* Research-002/tests/test_p55* Research-002/tests/test_p56* -v
```
Expected: 119 passed in ~90 seconds.

---

## 6. Verification of Governance Invariants
All operations assert that:
- `training_permitted = 0`
- `execution_authorized = 0`
- `gradient_steps = 0`
- `optimizer_updates = 0`
- `new_holdout_generation = NOT_AUTHORIZED`
- `production_keys = NOT_GENERATED`
- `production_vault = NOT_INITIALIZED`
- `final_evaluation = BLOCKED`
- Compromised Stage 3.5 holdout (`stage35_final_holdout`) permanently quarantined.
