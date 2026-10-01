import errno
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from app import mover
from app.operation_journal import OperationJournal, PhysicalPhase


EXIT_CODES = {
    "after_intent": 91,
    "after_same_rename": 92,
    "during_temp_copy": 93,
    "after_temp_verified": 94,
    "after_destination_published": 95,
    "after_destination_verified": 96,
    "after_source_staged": 97,
    "after_source_removed": 98,
    "before_metadata": 99,
}


def main() -> None:
    mode = sys.argv[1]
    root = Path(sys.argv[2]).resolve()
    source = root / "incoming" / "report.txt"
    organized = root / "organized"
    documents = organized / "documents"
    journal = OperationJournal(root / "data" / "operations.sqlite3")
    exit_code = EXIT_CODES[mode]
    original_transition = journal.transition_phase
    original_move = mover._move_no_clobber
    original_unlink = mover.os.unlink

    cross_modes = {
        "during_temp_copy",
        "after_temp_verified",
        "after_destination_published",
        "after_destination_verified",
        "after_source_staged",
        "after_source_removed",
    }

    def transition(operation_id, expected_phase, new_phase, **kwargs):
        result = original_transition(
            operation_id,
            expected_phase,
            new_phase,
            **kwargs,
        )
        targets = {
            "after_intent": PhysicalPhase.RENAME_INTENT,
            "after_temp_verified": PhysicalPhase.TEMP_VERIFIED,
            "after_destination_published": PhysicalPhase.DESTINATION_PUBLISHED,
            "after_destination_verified": PhysicalPhase.DESTINATION_VERIFIED,
            "after_source_staged": PhysicalPhase.SOURCE_STAGED,
            "before_metadata": PhysicalPhase.PHYSICAL_COMMITTED,
        }
        if targets.get(mode) is new_phase:
            os._exit(exit_code)
        return result

    def move_then_crash(path, destination):
        result = original_move(path, destination)
        if mode == "after_same_rename" and Path(path) == source:
            os._exit(exit_code)
        return result

    def partial_copy(source_handle, destination_handle):
        destination_handle.write(source_handle.read(7))
        destination_handle.flush()
        os.fsync(destination_handle.fileno())
        os._exit(exit_code)

    def unlink_then_crash(path, *args, **kwargs):
        result = original_unlink(path, *args, **kwargs)
        if mode == "after_source_removed" and Path(path).parent.name.startswith(
            ".filepilot-remove-"
        ):
            os._exit(exit_code)
        return result

    primitive_name = "rename" if os.name == "nt" else "link"
    real_primitive = getattr(mover.os, primitive_name)

    def cross_device(path, destination, *args, **kwargs):
        if Path(path) == source and Path(destination).is_relative_to(organized):
            raise OSError(errno.EXDEV, "simulated cross-volume move")
        return real_primitive(path, destination, *args, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(journal, "transition_phase", side_effect=transition)
        )
        if mode == "after_same_rename":
            stack.enter_context(
                patch.object(mover, "_move_no_clobber", side_effect=move_then_crash)
            )
        if mode in cross_modes:
            stack.enter_context(
                patch.object(mover.os, primitive_name, side_effect=cross_device)
            )
        if mode == "during_temp_copy":
            stack.enter_context(
                patch.object(mover, "_copy_file_data", side_effect=partial_copy)
            )
        if mode == "after_source_removed":
            stack.enter_context(
                patch.object(mover.os, "unlink", side_effect=unlink_then_crash)
            )

        mover.move_file_with_retries(
            source_file=source,
            destination_folders={
                "documents": str(documents),
                "others": str(organized / "others"),
            },
            extension_lookup={".txt": "documents"},
            stats_file=str(root / "reports" / "stats.json"),
            history_file=str(root / "reports" / "history.csv"),
            hash_db_file=str(root / "reports" / "hashes.json"),
            archive_by_date=False,
            rules={"documents": [".txt"]},
            organized_root=organized,
            retries=1,
            delay=0,
            category_override="documents",
            journal=journal,
        )
    raise RuntimeError(f"Crash point was not reached: {mode}")


if __name__ == "__main__":
    main()
