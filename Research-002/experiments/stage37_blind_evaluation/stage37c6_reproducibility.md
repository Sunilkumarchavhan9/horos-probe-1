# HOROS PROBE-1 — Stage 3.7C.6 Reproducibility Guide
## Attestation Acceptance Hardening & Independent Runtime Verification

## 1. Environment & Prerequisites
- Python 3.12+ with `pytest`, `torch`, `numpy`, `scipy==1.14.1`, and `cryptography`.
- Linux (Ubuntu 22.04+ recommended for container / namespace isolation) or Windows 11 with WSL2 / Docker Desktop.
- Isolated Test Key Architecture: Non-production test signing is isolated in `sign_attestation_digest_test_only()`. Production acceptance strictly requires an authorized production trust policy and rejects test signatures.

---

## 2. Test Execution Commands

### Run P58 Test Suite (23 Adversarial Tests)
```bash
PYTHONPATH=Research-002/src pytest Research-002/tests/test_p58_stage37c6_attestation_acceptance.py -v
```

### Run Full Regression Test Suite (P50–P58)
```bash
PYTHONPATH=Research-002/src pytest \
  Research-002/tests/test_p50_stage37a_protocol_hardening.py \
  Research-002/tests/test_p51_stage37b_evaluation_integration.py \
  Research-002/tests/test_p52_stage37c_security_acceptance.py \
  Research-002/tests/test_p53_stage37c1_security_closure.py \
  Research-002/tests/test_p54_stage37c2_runtime_isolation.py \
  Research-002/tests/test_p55_stage37c3_adversarial_validation.py \
  Research-002/tests/test_p56_stage37c4_cross_runtime_acceptance.py \
  Research-002/tests/test_p57_stage37c5_attestation_trust_closure.py \
  Research-002/tests/test_p58_stage37c6_attestation_acceptance.py -v
```

### Generate Official Attestation Report
```bash
PYTHONPATH=Research-002/src python Research-002/scripts/generate_attestation_report.py
```

---

## 3. Standalone Cryptographic Verification Commands

### A. Authorized Test Verification Pathway
Verifies the test-key signature and full canonical payload digest using the explicitly authorized test policy:
```bash
PYTHONPATH=Research-002/src python -c "
import json
from pathlib import Path
from world_model_vnext.security_attestation import (
    verify_attestation_digest,
    verify_test_attestation_acceptance,
)

report = json.loads(
    Path('Research-002/experiments/stage37_blind_evaluation/security_attestation_report.json').read_text(encoding='utf-8')
)

assert verify_attestation_digest(report), 'Attestation digest verification failed'
assert verify_test_attestation_acceptance(report), 'Test attestation acceptance failed'
print('Attestation test verification PASSED.')
"
```

### B. Production Acceptance Pathway (Strict Rejection of Test Records)
Proves that the generated test record is strictly rejected by production acceptance:
```bash
PYTHONPATH=Research-002/src python -c "
import json
from pathlib import Path
from world_model_vnext.contracts import ContractViolationError
from world_model_vnext.security_attestation import (
    verify_production_attestation_acceptance,
)

report = json.loads(
    Path('Research-002/experiments/stage37_blind_evaluation/security_attestation_report.json').read_text(encoding='utf-8')
)

try:
    verify_production_attestation_acceptance(report)
    assert False, 'Production acceptance should have rejected test signature!'
except ContractViolationError as exc:
    print(f'Production acceptance correctly rejected test record: {exc}')
"
```
