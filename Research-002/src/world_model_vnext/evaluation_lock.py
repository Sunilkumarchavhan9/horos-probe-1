"""Durable, atomic evaluation-consumption ledger and holdout release lock.

Implements Stage 3.7A Workstream A:
- Holdout releases are identified by canonical release ID or manifest SHA-256.
- A release may be reserved/consumed exactly ONCE across all checkpoints, architectures, and retries.
- Persists state in a durable, atomic JSON ledger with OS-level concurrency locking.
- State transitions: AVAILABLE -> RESERVED -> PRIVATE_DATA_ACCESS -> CONSUMED (or ABORTED).
- Once private data access begins, the release is permanently burned.
- Fails closed if the ledger is corrupted, unreadable, or tampered with.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

from .contracts import ContractViolationError


class HoldoutReleaseAlreadyConsumedError(ContractViolationError):
    """Raised when an evaluation is attempted against a consumed or burned holdout release."""


class HoldoutReleaseAlreadyReservedError(ContractViolationError):
    """Raised when an evaluation is attempted against a release currently reserved by another process."""


class LedgerCorruptedError(ContractViolationError):
    """Raised when the evaluation ledger schema, format, or content is invalid or corrupted."""


class DurableHoldoutLock:
    """Manages atomic reservation, state transitions, and audit trails for blind holdout releases."""

    def __init__(
        self,
        ledger_path: Path | str,
        audit_trail_path: Optional[Path | str] = None,
    ):
        self.ledger_path = Path(ledger_path)
        self.audit_trail_path = Path(audit_trail_path) if audit_trail_path else self.ledger_path.with_name("evaluation_audit_trail.jsonl")
        self._lock_path = self.ledger_path.with_suffix(".lock")
        
        # Ensure parent directory exists
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize ledger if it does not exist
        if not self.ledger_path.exists():
            self._write_fresh_ledger()
        else:
            self._verify_ledger_integrity()

    def _verify_ledger_integrity(self) -> Dict[str, Any]:
        """Reads and validates the ledger schema, failing closed on corruption."""
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            raise LedgerCorruptedError(f"Failed to read evaluation ledger at '{self.ledger_path}': {exc}") from exc

        if not isinstance(data, dict):
            raise LedgerCorruptedError("Ledger root must be a JSON object.")
        if "ledger_version" not in data or "releases" not in data:
            raise LedgerCorruptedError("Ledger missing required root fields ('ledger_version', 'releases').")
        if not isinstance(data["releases"], dict):
            raise LedgerCorruptedError("Ledger 'releases' must be a dictionary.")

        for release_id, entry in data["releases"].items():
            if not isinstance(entry, dict) or "status" not in entry:
                raise LedgerCorruptedError(f"Malformed release entry for '{release_id}'.")
            valid_statuses = {"RESERVED", "DATA_ACCESSED", "CONSUMED", "ABORTED"}
            if entry["status"] not in valid_statuses:
                raise LedgerCorruptedError(f"Invalid release status '{entry['status']}' for '{release_id}'.")

        return data

    def _write_fresh_ledger(self) -> None:
        """Initializes a new empty ledger."""
        fresh_data = {
            "ledger_version": "HOROS-HOLDOUT-LEDGER-V1",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "releases": {},
        }
        temp_path = self.ledger_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(fresh_data, f, indent=2)
        os.replace(temp_path, self.ledger_path)

    def _acquire_file_lock(self) -> Any:
        """Acquires an atomic OS-level file lock, preventing concurrent evaluators from racing."""
        import time
        max_attempts = 50
        for _ in range(max_attempts):
            try:
                # O_CREAT | O_EXCL provides atomic lock creation across Windows and POSIX
                fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                return fd
            except FileExistsError:
                time.sleep(0.05)
            except Exception as exc:
                raise LedgerCorruptedError(f"Failed to acquire lockfile '{self._lock_path}': {exc}") from exc
        raise HoldoutReleaseAlreadyReservedError(f"Timeout acquiring lockfile '{self._lock_path}'. Another evaluator is active.")

    def _release_file_lock(self, fd: Any) -> None:
        """Releases the atomic file lock."""
        try:
            os.close(fd)
            if self._lock_path.exists():
                os.remove(self._lock_path)
        except Exception:
            pass

    def _append_audit_event(self, event: Dict[str, Any]) -> None:
        """Appends an immutable event entry to the audit log."""
        event["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        line = json.dumps(event, sort_keys=True) + "\n"
        with open(self.audit_trail_path, "a", encoding="utf-8") as f:
            f.write(line)

    def reserve_release(
        self,
        holdout_release_id: str,
        checkpoint_sha256: str,
        protocol_version: str = "HOROS-P1-STAGE37-EVAL-V1",
        operator_id: str = "evaluator",
    ) -> None:
        """Atomically reserves a holdout release for evaluation. Fails closed if already reserved or consumed."""
        norm_release_id = str(holdout_release_id).strip().lower()
        norm_ckpt_sha = str(checkpoint_sha256).strip().lower()

        fd = self._acquire_file_lock()
        try:
            data = self._verify_ledger_integrity()
            releases = data["releases"]

            if norm_release_id in releases:
                existing = releases[norm_release_id]
                status = existing.get("status")
                if status in {"DATA_ACCESSED", "CONSUMED", "ABORTED"}:
                    raise HoldoutReleaseAlreadyConsumedError(
                        f"Holdout release '{holdout_release_id}' has already been {status.lower()} "
                        f"(initial checkpoint: {existing.get('checkpoint_sha256', '')[:8]}...). "
                        "Repeated evaluation of this blind holdout release is permanently prohibited."
                    )
                if status == "RESERVED":
                    raise HoldoutReleaseAlreadyReservedError(
                        f"Holdout release '{holdout_release_id}' is already reserved by another evaluation run."
                    )

            # Record reservation
            releases[norm_release_id] = {
                "release_id": norm_release_id,
                "status": "RESERVED",
                "checkpoint_sha256": norm_ckpt_sha,
                "protocol_version": protocol_version,
                "operator_id": operator_id,
                "reserved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "private_data_accessed": False,
            }

            # Atomic file update
            temp_path = self.ledger_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, self.ledger_path)

            self._append_audit_event({
                "action": "RELEASE_RESERVED",
                "release_id": norm_release_id,
                "checkpoint_sha256": norm_ckpt_sha,
                "protocol_version": protocol_version,
                "operator_id": operator_id,
            })
        finally:
            self._release_file_lock(fd)

    def record_private_data_access(self, holdout_release_id: str) -> None:
        """Transitions status to DATA_ACCESSED. Once called, release is burned and cannot be reset."""
        norm_release_id = str(holdout_release_id).strip().lower()
        fd = self._acquire_file_lock()
        try:
            data = self._verify_ledger_integrity()
            releases = data["releases"]
            if norm_release_id not in releases:
                raise ContractViolationError(f"Cannot record data access for unreserved release '{holdout_release_id}'.")
            
            entry = releases[norm_release_id]
            entry["status"] = "DATA_ACCESSED"
            entry["private_data_accessed"] = True
            entry["data_accessed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            temp_path = self.ledger_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, self.ledger_path)

            self._append_audit_event({
                "action": "PRIVATE_DATA_ACCESS_BEGUN",
                "release_id": norm_release_id,
                "checkpoint_sha256": entry.get("checkpoint_sha256"),
            })
        finally:
            self._release_file_lock(fd)

    def mark_consumed(self, holdout_release_id: str, report_digest: str) -> None:
        """Marks a holdout release as permanently CONSUMED with the final benchmark report hash."""
        norm_release_id = str(holdout_release_id).strip().lower()
        fd = self._acquire_file_lock()
        try:
            data = self._verify_ledger_integrity()
            releases = data["releases"]
            if norm_release_id not in releases:
                raise ContractViolationError(f"Cannot mark unreserved release '{holdout_release_id}' as consumed.")
            
            entry = releases[norm_release_id]
            entry["status"] = "CONSUMED"
            entry["report_digest"] = str(report_digest)
            entry["consumed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            temp_path = self.ledger_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, self.ledger_path)

            self._append_audit_event({
                "action": "RELEASE_CONSUMED",
                "release_id": norm_release_id,
                "report_digest": report_digest,
            })
        finally:
            self._release_file_lock(fd)

    def mark_aborted(self, holdout_release_id: str, reason: str) -> None:
        """Marks an evaluation run as ABORTED. If private data was accessed, the release remains burned."""
        norm_release_id = str(holdout_release_id).strip().lower()
        fd = self._acquire_file_lock()
        try:
            data = self._verify_ledger_integrity()
            releases = data["releases"]
            if norm_release_id not in releases:
                raise ContractViolationError(f"Cannot abort unreserved release '{holdout_release_id}'.")
            
            entry = releases[norm_release_id]
            entry["status"] = "ABORTED"
            entry["abort_reason"] = str(reason)
            entry["aborted_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            temp_path = self.ledger_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, self.ledger_path)

            self._append_audit_event({
                "action": "RELEASE_ABORTED",
                "release_id": norm_release_id,
                "reason": reason,
                "data_was_accessed": entry.get("private_data_accessed", False),
            })
        finally:
            self._release_file_lock(fd)

    def assert_release_available(self, holdout_release_id: str) -> None:
        """Asserts that a holdout release is unreserved and unconsumed."""
        norm_release_id = str(holdout_release_id).strip().lower()
        data = self._verify_ledger_integrity()
        releases = data.get("releases", {})
        if norm_release_id in releases:
            entry = releases[norm_release_id]
            status = entry.get("status")
            raise HoldoutReleaseAlreadyConsumedError(
                f"Release '{holdout_release_id}' is not available (status: {status}). "
                "Repeated evaluation of this blind holdout release is prohibited."
            )
