"""Import the archived plants without modifying them."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE.parent / "feasibility_aware_recovery_p5_t4"
REVISION = HERE.parent / "feasibility_aware_recovery_p5_t4_revision"


def install() -> None:
    ordered = [str(HERE), str(REVISION), str(ARCHIVE)]
    for entry in ordered:
        while entry in sys.path:
            sys.path.remove(entry)
    for entry in reversed(ordered):
        sys.path.insert(0, entry)


install()
