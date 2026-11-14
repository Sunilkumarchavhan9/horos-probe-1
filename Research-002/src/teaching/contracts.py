"""Versioned sensor and wrench contracts for HOROS PROBE-1 Research-002."""

TASK1_WRENCH_CONTRACT = "peg_in_hole_ati_wrench_v1"
TASK2_WRENCH_CONTRACT = "task2_cartesian_resultant_wrench_v1"
LEGACY_TASK2_WRENCH_CONTRACT = "legacy_task2_normal_force_padding_v0"

TASK2_TASK_IDS = frozenset({"compliant_prelift", "compliant_lift", "multifinger_lift"})


def expected_wrench_contract(task: str) -> str:
    """Return the required wrench contract for a Research-002 task identifier."""
    if task == "peg_in_hole":
        return TASK1_WRENCH_CONTRACT
    if task in TASK2_TASK_IDS:
        return TASK2_WRENCH_CONTRACT
    raise ValueError(f"Unsupported Research-002 task identifier: {task!r}")
