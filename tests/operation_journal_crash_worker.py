import os
import sqlite3
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.operation_journal import (  # noqa: E402
    OperationJournal,
    PhysicalPhase,
)


COMMITTED_EXIT_CODE = 83
UNCOMMITTED_EXIT_CODE = 84


def _commit_then_exit(database_path: Path, root: Path) -> None:
    journal = OperationJournal(database_path)
    operation = journal.create_operation(
        source_path=root / "incoming.txt",
        organized_root=root / "organized",
        intended_destination=root / "organized" / "documents" / "incoming.txt",
    )
    journal.transition_phase(
        operation.operation_id,
        expected_phase=PhysicalPhase.PREPARED,
        new_phase=PhysicalPhase.RENAME_INTENT,
    )
    (root / "committed-operation-id.txt").write_text(
        operation.operation_id,
        encoding="ascii",
    )
    os._exit(COMMITTED_EXIT_CODE)


def _leave_transaction_uncommitted(database_path: Path, root: Path) -> None:
    operation_id = (root / "uncommitted-operation-id.txt").read_text(
        encoding="ascii"
    )
    connection = sqlite3.connect(
        database_path,
        timeout=5,
        isolation_level=None,
    )
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        """
        UPDATE operations
        SET physical_phase = ?, updated_at_utc = ?
        WHERE operation_id = ? AND physical_phase = ?
        """,
        (
            PhysicalPhase.RENAME_INTENT.value,
            "2099-01-01T00:00:00.000000Z",
            operation_id,
            PhysicalPhase.PREPARED.value,
        ),
    )
    connection.execute(
        """
        INSERT INTO operation_events (
            operation_id,
            sequence_number,
            event_kind,
            from_phase,
            to_phase,
            from_status,
            to_status,
            occurred_at_utc
        ) VALUES (?, 2, 'phase_transition', ?, ?, 'open', 'open', ?)
        """,
        (
            operation_id,
            PhysicalPhase.PREPARED.value,
            PhysicalPhase.RENAME_INTENT.value,
            "2099-01-01T00:00:00.000000Z",
        ),
    )
    (root / "uncommitted-write-reached.txt").write_text("yes", encoding="ascii")
    os._exit(UNCOMMITTED_EXIT_CODE)


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: operation_journal_crash_worker.py MODE DATABASE ROOT"
        )
    mode = sys.argv[1]
    database_path = Path(sys.argv[2]).resolve()
    root = Path(sys.argv[3]).resolve()
    if mode == "committed":
        _commit_then_exit(database_path, root)
    elif mode == "uncommitted":
        _leave_transaction_uncommitted(database_path, root)
    else:
        raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()
