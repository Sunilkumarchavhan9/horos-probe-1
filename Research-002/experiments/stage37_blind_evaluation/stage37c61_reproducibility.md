# HOROS PROBE-1 — Stage 3.7C.6.1 Reproducibility Guide
## Independent Verification of Post-Merge Trust Corrections

### 1. Environment & Setup

Clone the repository and ensure you are on branch `fix/probe1-stage37c6-postmerge-trust-fixes`:

```bash
git clone https://github.com/Sunilkumarchavhan9/horos-probe-1.git
cd horos-probe-1
git checkout fix/probe1-stage37c6-postmerge-trust-fixes
```

Ensure Python 3.10+ is active and dependencies are installed (`torch`, `cryptography`, `pytest`, `anyio`):

```bash
# PowerShell
$env:PYTHONPATH="Research-002/src"

# Linux / Bash
export PYTHONPATH="Research-002/src"
```

---

### 2. Independent Test Execution

#### Step 1: Execute Focused Stage 3.7C.6.1 Adversarial Suite (P59)
Runs the 19 post-merge trust correction tests:

```bash
python -m pytest Research-002/tests/test_p59_stage37c6_postmerge_trust_fixes.py -v
```
Expected output: `19 passed`.

#### Step 2: Execute Stage 3.7C.6 Hardening Suite (P58)
Runs the 23 attestation acceptance hardening tests:

```bash
python -m pytest Research-002/tests/test_p58_stage37c6_attestation_acceptance.py -v
```
Expected output: `23 passed`.

#### Step 3: Execute Prior Regression Suites (P56, P57)
Runs cross-runtime acceptance and attestation trust closure tests:

```bash
python -m pytest Research-002/tests/test_p56_stage37c4_cross_runtime_acceptance.py Research-002/tests/test_p57_stage37c5_attestation_trust_closure.py -v
```
Expected output: `26 passed`.

#### Step 4: Execute Complete Repository Test Suite
Executes all 588 repository tests:

```bash
python -m pytest Research-002/tests -q
```
Expected output: `588 passed in ~230s`.

---

### 3. Regenerate & Validate Security Attestation Report

Generate the machine-readable attestation report:

```bash
python Research-002/scripts/generate_attestation_report.py
```

Verify that the generated report validates cleanly under test policy:

```bash
python -c "
import json
from world_model_vnext.security_attestation import verify_test_attestation_acceptance
with open('Research-002/experiments/stage37_blind_evaluation/security_attestation_report.json', 'r', encoding='utf-8') as f:
    rec = json.load(f)
assert verify_test_attestation_acceptance(rec) is True
print('Attestation report verified successfully!')
"
```

---

### 4. Governance Invariant Verification

Verify that all governance restrictions remain locked to zero:

```bash
python -c "
from world_model_vnext.runner import TRAINING_PERMITTED, EXECUTION_AUTHORIZED
from world_model_vnext.contracts import COMPROMISED_HOLDOUT_ID, COMPROMISED_HOLDOUT_STATUS
assert TRAINING_PERMITTED == 0, 'training_permitted must be 0'
assert EXECUTION_AUTHORIZED == 0, 'execution_authorized must be 0'
assert COMPROMISED_HOLDOUT_ID == 'stage35_final_holdout'
assert COMPROMISED_HOLDOUT_STATUS == 'COMPROMISED_PUBLIC_EXPOSURE'
print('Governance invariants strictly locked: ALL OK')
"
```
