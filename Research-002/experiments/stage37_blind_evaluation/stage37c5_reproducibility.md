# HOROS PROBE-1 — Stage 3.7C.5 Reproducibility Guide

## 1. Environment & Prerequisites
- Python 3.12+ with `pytest`, `torch`, `numpy`, `scipy==1.14.1`, and `cryptography`.
- Linux (Ubuntu 22.04+ recommended for full container / namespace isolation) or Windows 11 with WSL2.
- Dedicated Non-Production Test Key: Built into `world_model_vnext.security_attestation` (derived deterministically from seed; production keys remain ungenerated).

## 2. Test Execution Commands

### Run P57 Test Suite
```bash
PYTHONPATH=Research-002:Research-002/src pytest Research-002/tests/test_p57_stage37c5_attestation_trust_closure.py -v
```

### Run Full Regression Suite (P50–P57)
```bash
PYTHONPATH=Research-002:Research-002/src pytest \
  Research-002/tests/test_p50_stage37a_protocol_hardening.py \
  Research-002/tests/test_p51_stage37b_evaluation_integration.py \
  Research-002/tests/test_p52_stage37c_security_acceptance.py \
  Research-002/tests/test_p53_stage37c1_security_closure.py \
  Research-002/tests/test_p54_stage37c2_runtime_isolation.py \
  Research-002/tests/test_p55_stage37c3_adversarial_validation.py \
  Research-002/tests/test_p56_stage37c4_cross_runtime_acceptance.py \
  Research-002/tests/test_p57_stage37c5_attestation_trust_closure.py -v
```

### Generate Verified Security Attestation Report
```bash
PYTHONPATH=Research-002:Research-002/src python Research-002/scripts/generate_attestation_report.py
```

## 3. Cryptographic Verification Command
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
print('Attestation verification completed')
"
```
