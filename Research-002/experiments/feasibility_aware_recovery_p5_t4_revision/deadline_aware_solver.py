"""Time the one-shot trajectory search. Later steps reuse the command and do not solve again."""

from __future__ import annotations

import time
from typing import Dict, Optional

from recovery_trajectory_optimizer import select_command


def solve_with_deadline(obs: Dict[str, object], static_fn, mode: str, budget: Optional[float], started: float) -> Dict[str, object]:
    try:
        chosen = select_command(obs, static_fn, mode, budget)
        chosen["deadline_fallback"] = False
    except Exception:
        chosen = select_command(obs, static_fn, "fixed_margin", budget)
        chosen["deadline_fallback"] = True
    chosen["solver_ms"] = (time.perf_counter() - started) * 1000.0
    return chosen
