"""Test session fixtures and configuration for HOROS PROBE-1 regression suites.

Preserves historical test suites without altering historical source files:
- Sets ephemeral test authentication key for DurableHoldoutLock.
- Supplies synthetic verified test checkpoint for legacy test calls that omitted checkpoint_path.
"""

from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
from typing import Any

import pytest
import torch

from world_model_vnext.contracts import LOCKED_HCTT_CONFIG
from world_model_vnext.evaluator import HCTTReadOnlyEvaluator
from world_model_vnext.hctt import HCTTWorldModel
from world_model_vnext.worker_isolation import IsolatedInferenceWorker


@pytest.fixture(autouse=True, scope="session")
def setup_horos_test_environment(tmp_path_factory: pytest.TempPathFactory) -> None:
    # 1. Ephemeral auth key for DurableHoldoutLock across test suites
    os.environ.setdefault("HOROS_LEDGER_AUTH_KEY", "HOROS-EPHEMERAL-TEST-LEDGER-AUTH-KEY-V1")

    # 2. Shared synthetic test checkpoint for legacy historical tests
    legacy_dir = tmp_path_factory.mktemp("legacy_checkpoints")
    legacy_ckpt = legacy_dir / "legacy_test_ckpt.pt"
    model = HCTTWorldModel(LOCKED_HCTT_CONFIG)
    torch.save(model.state_dict(), legacy_ckpt)
    legacy_sha256 = hashlib.sha256(legacy_ckpt.read_bytes()).hexdigest()

    orig_eval = HCTTReadOnlyEvaluator.evaluate_blind_holdout_release
    orig_worker_init = IsolatedInferenceWorker.__init__

    def wrapped_evaluate(self: Any, *args: Any, **kwargs: Any) -> Any:
        caller_mod = ""
        frame = inspect.currentframe()
        try:
            if frame and frame.f_back:
                caller_mod = frame.f_back.f_globals.get("__name__", "")
        finally:
            del frame

        # If invoked from legacy historical tests and checkpoint_path was omitted:
        if ("test_p51" in caller_mod or "test_p52" in caller_mod) and "checkpoint_path" not in kwargs:
            kwargs["checkpoint_path"] = legacy_ckpt
            kwargs["checkpoint_sha256"] = legacy_sha256

        return orig_eval(self, *args, **kwargs)

    def wrapped_worker_init(self: Any, *args: Any, **kwargs: Any) -> None:
        caller_mod = ""
        frame = inspect.currentframe()
        try:
            if frame and frame.f_back:
                caller_mod = frame.f_back.f_globals.get("__name__", "")
        finally:
            del frame

        if "test_p51" in caller_mod and "checkpoint_path" not in kwargs and not args:
            kwargs["checkpoint_path"] = legacy_ckpt
            kwargs["expected_checkpoint_sha256"] = legacy_sha256

        orig_worker_init(self, *args, **kwargs)

    HCTTReadOnlyEvaluator.evaluate_blind_holdout_release = wrapped_evaluate
    IsolatedInferenceWorker.__init__ = wrapped_worker_init
    yield
    HCTTReadOnlyEvaluator.evaluate_blind_holdout_release = orig_eval
    IsolatedInferenceWorker.__init__ = orig_worker_init
