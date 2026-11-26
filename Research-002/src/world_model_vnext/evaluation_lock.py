"""Durable, atomic evaluation-consumption ledger and holdout release lock.

Implements Stage 3.7C Workstream H:
- Holdout releases are identified by canonical release ID or manifest SHA-256.
- A release may be reserved/consumed exactly ONCE across all checkpoints, architectures, and retries.
- Persists state in a durable, atomic JSON ledger with OS-level concurrency locking and fsync persistence.
- Authenticates ledger state with HMAC-SHA256 to detect structurally valid status tampering.
- Maintains a cryptographic hash-chain and HMAC authentication over append-only audit trail events.
- Detects deletion, insertion, or reordering of historical evaluation events.
- Fails closed if the ledger is missing, corrupted, unreadable, or tampered with.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .contracts import ContractViolationError


class HoldoutReleaseAlreadyConsumedError(ContractViolationError):
    """Raised when an evaluation is attempted against a consumed or burned holdout release."""


class HoldoutReleaseAlreadyReservedError(ContractViolationError):
    """Raised when an evaluation is attempted against a release currently reserved by another process."""


class LedgerCorruptedError(ContractViolationError):
    """Raised when the evaluation ledger schema, format, or content is invalid or corrupted."""


class LedgerMissingError(ContractViolationError):
    """Raised when an expected production evaluation ledger is missing."""


def _canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


class DurableHoldoutLock:
    """Manages atomic reservation, state transitions, and audit trails for blind holdout releases."""

    @classmethod
    def initialize_fresh_ledger(
        cls,
        ledger_path: Path | str,
        audit_trail_path: Optional[Path | str] = None,
        auth_key: Optional[bytes] = None,
    ) -> "DurableHoldoutLock":
        """Explicitly initializes a new empty ledger file. Synthetic tests and administrative setups call this."""
        return cls(ledger_path, audit_trail_path=audit_trail_path, allow_create=True, auth_key=auth_key)

    @classmethod
    def open_production_ledger(
        cls,
        ledger_path: Path | str,
        audit_trail_path: Optional[Path | str] = None,
        auth_key: Optional[bytes] = None,
    ) -> "DurableHoldoutLock":
        """Opens and verifies an existing production ledger. Fails closed if missing, corrupt, or unreadable."""
        return cls(ledger_path, audit_trail_path=audit_trail_path, allow_create=False, auth_key=auth_key)

    def __init__(
        self,
        ledger_path: Path | str,
        audit_trail_path: Optional[Path | str] = None,
        allow_create: bool = False,
        auth_key: Optional[bytes] = None,
    ):
        self.ledger_path = Path(ledger_path)
        self.audit_trail_path = Path(audit_trail_path) if audit_trail_path else self.ledger_path.with_name("evaluation_audit_trail.jsonl")
        self._lock_path = self.ledger_path.with_suffix(".lock")

        if auth_key is not None:
            if isinstance(auth_key, str):
                self.auth_key = auth_key.encode("utf-8")
            elif isinstance(auth_key, bytes):
                self.auth_key = auth_key
            else:
                raise ContractViolationError(f"Unsupported auth_key type: {type(auth_key)}")
        else:
            env_key = os.environ.get("HOROS_LEDGER_AUTH_KEY", "").encode("utf-8")
            self.auth_key = env_key if env_key else b""

        if not self.auth_key:
            raise ContractViolationError(
                "Mandatory ledger authentication key is missing. Production ledger operations require an externally "
                "provisioned secret via 'HOROS_LEDGER_AUTH_KEY' or an explicit ephemeral 'auth_key'. "
                "Hardcoded secret fallback is prohibited."
            )
        
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        
        if not self.ledger_path.exists():
            if not allow_create:
                raise LedgerMissingError(
                    f"Evaluation ledger missing at '{self.ledger_path}'. "
                    "Normal evaluator startup requires an existing verified production ledger. "
                    "Automatic empty ledger creation is prohibited to prevent forgetting consumed releases."
                )
            self._write_fresh_ledger()
        else:
            self._verify_ledger_integrity()

    def _compute_state_hmac(self, releases: Dict[str, Any]) -> str:
        """Computes HMAC-SHA256 across canonical bytes of the releases dictionary."""
        raw_bytes = _canonical_json_bytes(releases)
        return hmac.new(self.auth_key, raw_bytes, hashlib.sha256).hexdigest()

    def _compute_event_hmac(self, event_data: Dict[str, Any]) -> str:
        """Computes HMAC-SHA256 across the canonical fields of an audit event."""
        raw = f"{event_data.get('event_seq', 0)}:{event_data.get('action')}:{event_data.get('release_id')}:{event_data.get('prev_hash')}:{event_data.get('timestamp')}"
        return hmac.new(self.auth_key, raw.encode("utf-8"), hashlib.sha256).hexdigest()

    def _verify_audit_trail(self, expected_chain_head: Optional[str]) -> None:
        """Verifies sequential integrity, hash linking, and HMAC signatures of the audit event log."""
        if not self.audit_trail_path.exists():
            if expected_chain_head is not None:
                raise LedgerCorruptedError("Audit trail file missing but ledger specifies audit_chain_head.")
            return

        with open(self.audit_trail_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]

        if not lines:
            if expected_chain_head is not None:
                raise LedgerCorruptedError("Empty audit trail file with non-null ledger audit_chain_head.")
            return

        prev_hash = "0" * 64
        for idx, line in enumerate(lines):
            try:
                ev = json.loads(line)
            except Exception as exc:
                raise LedgerCorruptedError(f"Corrupt JSON at audit event {idx}: {exc}") from exc

            # Verify sequence continuity (detects event deletion or insertion)
            seq = ev.get("event_seq")
            if seq != idx:
                raise LedgerCorruptedError(
                    f"Audit event sequence violation at index {idx}: expected {idx}, got {seq}. "
                    "Event deletion or reordering detected!"
                )

            # Verify cryptographic hash linking (detects reordering, tampering, or deletion)
            ev_prev = ev.get("prev_hash")
            if ev_prev != prev_hash:
                raise LedgerCorruptedError(
                    f"Audit event hash chain broken at index {idx}: expected prev_hash '{prev_hash}', got '{ev_prev}'. "
                    "Audit event reordering or tampering detected!"
                )

            # Verify event HMAC signature
            ev_hmac = ev.get("event_hmac")
            expected_hmac = self._compute_event_hmac(ev)
            if not hmac.compare_digest(str(ev_hmac), expected_hmac):
                raise LedgerCorruptedError(
                    f"Audit event HMAC validation failed at index {idx}. Event content has been tampered with!"
                )

            # Compute SHA-256 of the exact line bytes as prev_hash for next event
            line_bytes = line.encode("utf-8")
            prev_hash = hashlib.sha256(line_bytes).hexdigest()

        if expected_chain_head is not None and prev_hash != expected_chain_head:
            raise LedgerCorruptedError(
                f"Audit chain head mismatch: ledger recorded '{expected_chain_head}', audit log ended at '{prev_hash}'."
            )

    def _verify_ledger_integrity(self) -> Dict[str, Any]:
        """Reads and validates schema, HMAC authentication, and audit chain integrity."""
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

        # Validate entries and allowed statuses
        for release_id, entry in data["releases"].items():
            if not isinstance(entry, dict) or "status" not in entry:
                raise LedgerCorruptedError(f"Malformed release entry for '{release_id}'.")
            valid_statuses = {"RESERVED", "DATA_ACCESS_AUTHORIZED", "DATA_ACCESSED", "CONSUMED", "ABORTED"}
            if entry["status"] not in valid_statuses:
                raise LedgerCorruptedError(f"Invalid release status '{entry['status']}' for '{release_id}'.")

        # Verify state HMAC authentication (detects structurally valid status modifications)
        recorded_hmac = data.get("state_hmac")
        if not recorded_hmac:
            raise LedgerCorruptedError("Ledger missing mandatory 'state_hmac' authentication anchor.")
        expected_hmac = self._compute_state_hmac(data["releases"])
        if not hmac.compare_digest(recorded_hmac, expected_hmac):
            raise LedgerCorruptedError(
                "Evaluation ledger state HMAC verification failed! "
                "Unauthorized modification or schema-valid tampering detected."
            )

        # Verify audit trail hash chain
        self._verify_audit_trail(data.get("audit_chain_head"))

        return data

    def _write_fresh_ledger(self) -> None:
        """Initializes a new empty authenticated ledger and audit log."""
        releases: Dict[str, Any] = {}
        state_hmac = self._compute_state_hmac(releases)
        fresh_data = {
            "ledger_version": "HOROS-HOLDOUT-LEDGER-V2",
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "releases": releases,
            "state_hmac": state_hmac,
            "audit_chain_head": None,
        }
        self._persist_atomic_ledger(fresh_data)

    def _persist_atomic_ledger(self, data: Dict[str, Any]) -> None:
        """Atomically writes ledger to disk with flush, fsync, and replace to prevent partial writes."""
        temp_path = self.ledger_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, self.ledger_path)

    def _acquire_file_lock(self) -> Any:
        """Acquires an atomic OS-level file lock, preventing concurrent evaluators from racing."""
        import time
        max_attempts = 100
        for _ in range(max_attempts):
            try:
                fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except Exception:
                    os.close(fd)
                    try:
                        os.remove(self._lock_path)
                    except Exception:
                        pass
                    raise FileExistsError("Lock acquisition collided")
                return fd
            except (FileExistsError, OSError):
                time.sleep(0.05)
            except Exception as exc:
                raise LedgerCorruptedError(f"Failed to acquire lockfile '{self._lock_path}': {exc}") from exc
        raise HoldoutReleaseAlreadyReservedError(f"Timeout acquiring lockfile '{self._lock_path}'. Another evaluator is active.")

    def _release_file_lock(self, fd: Any) -> None:
        """Releases the atomic file lock."""
        try:
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except Exception:
                    pass
            os.close(fd)
            if self._lock_path.exists():
                os.remove(self._lock_path)
        except Exception:
            pass

    def _append_audit_event(self, event: Dict[str, Any]) -> str:
        """Appends an authenticated, hash-chained event entry to the audit log, returning its hash."""
        event_copy = dict(event)
        event_copy["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        # Determine current chain head and next sequence
        prev_hash = "0" * 64
        seq = 0
        if self.audit_trail_path.exists():
            with open(self.audit_trail_path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip()]
            if lines:
                seq = len(lines)
                prev_hash = hashlib.sha256(lines[-1].encode("utf-8")).hexdigest()

        event_copy["event_seq"] = seq
        event_copy["prev_hash"] = prev_hash
        event_copy["event_hmac"] = self._compute_event_hmac(event_copy)

        line = json.dumps(event_copy, sort_keys=True) + "\n"
        with open(self.audit_trail_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

        return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()

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
                if status in {"DATA_ACCESS_AUTHORIZED", "DATA_ACCESSED", "CONSUMED", "ABORTED"}:
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

            chain_head = self._append_audit_event({
                "action": "RELEASE_RESERVED",
                "release_id": norm_release_id,
                "checkpoint_sha256": norm_ckpt_sha,
                "protocol_version": protocol_version,
                "operator_id": operator_id,
            })

            data["state_hmac"] = self._compute_state_hmac(releases)
            data["audit_chain_head"] = chain_head
            self._persist_atomic_ledger(data)
        finally:
            self._release_file_lock(fd)

    def authorize_data_access(self, holdout_release_id: str) -> None:
        """Transitions status from RESERVED to DATA_ACCESS_AUTHORIZED."""
        norm_release_id = str(holdout_release_id).strip().lower()
        fd = self._acquire_file_lock()
        try:
            data = self._verify_ledger_integrity()
            releases = data["releases"]
            if norm_release_id not in releases:
                raise ContractViolationError(f"Cannot authorize data access for unreserved release '{holdout_release_id}'.")
            
            entry = releases[norm_release_id]
            status = entry.get("status")
            if status != "RESERVED":
                raise ContractViolationError(
                    f"Cannot authorize data access for release '{holdout_release_id}' in state '{status}'. Expected 'RESERVED'."
                )
            entry["status"] = "DATA_ACCESS_AUTHORIZED"
            entry["data_access_authorized_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            chain_head = self._append_audit_event({
                "action": "DATA_ACCESS_AUTHORIZED",
                "release_id": norm_release_id,
                "checkpoint_sha256": entry.get("checkpoint_sha256"),
            })

            data["state_hmac"] = self._compute_state_hmac(releases)
            data["audit_chain_head"] = chain_head
            self._persist_atomic_ledger(data)
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
            status = entry.get("status")
            if status not in {"RESERVED", "DATA_ACCESS_AUTHORIZED"}:
                raise ContractViolationError(
                    f"Cannot record data access for release '{holdout_release_id}' in state '{status}'."
                )
            entry["status"] = "DATA_ACCESSED"
            entry["private_data_accessed"] = True
            entry["data_accessed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            chain_head = self._append_audit_event({
                "action": "PRIVATE_DATA_ACCESS_BEGUN",
                "release_id": norm_release_id,
                "checkpoint_sha256": entry.get("checkpoint_sha256"),
            })

            data["state_hmac"] = self._compute_state_hmac(releases)
            data["audit_chain_head"] = chain_head
            self._persist_atomic_ledger(data)
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

            chain_head = self._append_audit_event({
                "action": "RELEASE_CONSUMED",
                "release_id": norm_release_id,
                "report_digest": report_digest,
            })

            data["state_hmac"] = self._compute_state_hmac(releases)
            data["audit_chain_head"] = chain_head
            self._persist_atomic_ledger(data)
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

            chain_head = self._append_audit_event({
                "action": "RELEASE_ABORTED",
                "release_id": norm_release_id,
                "reason": reason,
                "data_was_accessed": entry.get("private_data_accessed", False),
            })

            data["state_hmac"] = self._compute_state_hmac(releases)
            data["audit_chain_head"] = chain_head
            self._persist_atomic_ledger(data)
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
