"""Put the archived P5-T4 package on the import path without modifying it."""

from __future__ import annotations

import sys
from pathlib import Path

REVISION_DIR = Path(__file__).resolve().parent
ARCHIVE_DIR = REVISION_DIR.parent / "feasibility_aware_recovery_p5_t4"


def install() -> None:
    for entry in (str(REVISION_DIR), str(ARCHIVE_DIR)):
        if entry not in sys.path:
            sys.path.insert(0, entry)


install()
