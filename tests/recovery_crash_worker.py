import errno
import json
import os
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app import mover  # noqa: E402
from app.watcher import FileMonitor, NewFileHandler  # noqa: E402


EXIT_CODES = {
    "same_volume_physical": 71,
    "cross_temp_partial": 72,
    "cross_temp_verified": 73,
    "cross_published": 74,
    "cross_verified": 75,
    "cross_source_staged": 76,
    "source_removed_pre_hash": 77,
    "hash_pre_history": 78,
    "history_pre_stats": 79,
    "stats_pre_callback": 80,
}


def _paths(root: Path) -> dict[str, Path]:
    return {
        "source": root / "incoming" / "incoming.txt",
        "organized": root / "organized",
        "documents": root / "organized" / "documents",
        "hashes": root / "reports" / "hashes.json",
        "history": root / "reports" / "history.csv",
        "stats": root / "reports" / "stats.json",
    }


def _move(root: Path):
    paths = _paths(root)
    return mover.move_file_with_retries(
        source_file=paths["source"],
        destination_folders={
            "documents": str(paths["documents"]),
            "others": str(paths["organized"] / "others"),
        },
        extension_lookup={".txt": "documents"},
        stats_file=str(paths["stats"]),
        history_file=str(paths["history"]),
        hash_db_file=str(paths["hashes"]),
        archive_by_date=False,
        rules={"documents": [".txt"]},
        organized_root=paths["organized"],
        retries=1,
        delay=0,
        category_override="documents",
    )


def _crash(root: Path, scenario: str) -> None:
    exit_code = EXIT_CODES[scenario]
    marker = root / "crash-marker.json"
    marker.write_text(
        json.dumps({"scenario": scenario, "exit_code": exit_code}),
        encoding="utf-8",
    )
    os._exit(exit_code)


@contextmanager
def _force_cross_volume(root: Path, scenario: str):
    paths = _paths(root)
    primitive_name = "rename" if os.name == "nt" else "link"
    real_primitive = getattr(mover.os, primitive_name)
    real_rename = mover.os.rename

    def cross_device_primitive(source, destination, *args, **kwargs):
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            source_path == paths["source"]
            and destination_path.is_relative_to(paths["organized"])
        ):
            raise OSError(errno.EXDEV, "simulated cross-device boundary")
        if (
            scenario == "cross_source_staged"
            and destination_path.parent.name.startswith(".filepilot-remove-")
        ):
            result = real_primitive(source, destination, *args, **kwargs)
            _crash(root, scenario)
            return result
        return real_primitive(source, destination, *args, **kwargs)

    def staging_rename(source, destination, *args, **kwargs):
        destination_path = Path(destination)
        result = real_rename(source, destination, *args, **kwargs)
        if (
            scenario == "cross_source_staged"
            and destination_path.parent.name.startswith(".filepilot-remove-")
        ):
            _crash(root, scenario)
        return result

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                mover.os,
                primitive_name,
                side_effect=cross_device_primitive,
            )
        )
        if primitive_name != "rename" and scenario == "cross_source_staged":
            stack.enter_context(
                patch.object(mover.os, "rename", side_effect=staging_rename)
            )
        yield


def _run_crash_scenario(root: Path, scenario: str) -> None:
    if scenario == "same_volume_physical":
        real_move = mover._move_no_clobber

        def crash_after_move(source, destination):
            result = real_move(source, destination)
            _crash(root, scenario)
            return result

        with patch("app.mover._move_no_clobber", side_effect=crash_after_move):
            _move(root)

    elif scenario == "cross_temp_partial":
        def partial_copy(source_handle, destination_handle):
            partial = source_handle.read(7)
            destination_handle.write(partial)
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
            _crash(root, scenario)

        with (
            _force_cross_volume(root, scenario),
            patch("app.mover._copy_file_data", side_effect=partial_copy),
        ):
            _move(root)

    elif scenario == "cross_temp_verified":
        def crash_before_publish(*_args, **_kwargs):
            _crash(root, scenario)

        with (
            _force_cross_volume(root, scenario),
            patch(
                "app.mover._publish_temporary_no_clobber",
                side_effect=crash_before_publish,
            ),
        ):
            _move(root)

    elif scenario == "cross_published":
        real_publish = mover._publish_temporary_no_clobber

        def crash_after_publish(*args, **kwargs):
            result = real_publish(*args, **kwargs)
            _crash(root, scenario)
            return result

        with (
            _force_cross_volume(root, scenario),
            patch(
                "app.mover._publish_temporary_no_clobber",
                side_effect=crash_after_publish,
            ),
        ):
            _move(root)

    elif scenario == "cross_verified":
        def crash_before_source_removal(*_args, **_kwargs):
            _crash(root, scenario)

        with (
            _force_cross_volume(root, scenario),
            patch(
                "app.mover._remove_verified_source",
                side_effect=crash_before_source_removal,
            ),
        ):
            _move(root)

    elif scenario == "cross_source_staged":
        with _force_cross_volume(root, scenario):
            _move(root)

    elif scenario == "source_removed_pre_hash":
        def crash_before_registration(*_args, **_kwargs):
            _crash(root, scenario)

        with (
            _force_cross_volume(root, scenario),
            patch(
                "app.mover.register_file_hash",
                side_effect=crash_before_registration,
            ),
        ):
            _move(root)

    elif scenario == "hash_pre_history":
        real_register = mover.register_file_hash

        def crash_after_registration(*args, **kwargs):
            real_register(*args, **kwargs)
            _crash(root, scenario)

        with patch(
            "app.mover.register_file_hash",
            side_effect=crash_after_registration,
        ):
            _move(root)

    elif scenario == "history_pre_stats":
        real_append = mover.append_history

        def crash_after_history(*args, **kwargs):
            real_append(*args, **kwargs)
            _crash(root, scenario)

        with patch(
            "app.mover.append_history",
            side_effect=crash_after_history,
        ):
            _move(root)

    elif scenario == "stats_pre_callback":
        paths = _paths(root)
        callback_marker = root / "callback-delivered.json"
        result_marker = root / "processing-returned.json"
        real_update = mover.update_stats

        def callback(filename, category, status):
            callback_marker.write_text(
                json.dumps(
                    {"filename": filename, "category": category, "status": status}
                ),
                encoding="utf-8",
            )

        def crash_after_stats(*args, **kwargs):
            real_update(*args, **kwargs)
            _crash(root, scenario)

        handler = NewFileHandler(
            {
                "destination_folders": {
                    "documents": str(paths["documents"]),
                    "others": str(paths["organized"] / "others"),
                },
                "organized_base_folder": str(paths["organized"]),
                "rules": {"documents": [".txt"]},
                "processing_wait_seconds": 0,
                "duplicate_event_window_seconds": 0,
                "archive_by_date": False,
                "stats_file": str(paths["stats"]),
                "history_file": str(paths["history"]),
                "hash_db_file": str(paths["hashes"]),
            },
            {".txt": "documents"},
            file_processed_callback=callback,
        )
        with (
            patch.object(handler, "_wait_until_stable", return_value=True),
            patch("app.watcher.smart_classify", return_value=None),
            patch("app.mover.update_stats", side_effect=crash_after_stats),
        ):
            handler._process_file_thread(str(paths["source"]), "startup_scan")
        result_marker.write_text("returned", encoding="utf-8")
    else:
        raise ValueError(f"Unknown crash scenario: {scenario}")

    raise RuntimeError(f"Crash point was not reached: {scenario}")


def _scan_startup(root: Path) -> None:
    paths = _paths(root)
    monitor = FileMonitor(
        {
            "source_folder": str(root / "incoming"),
            "organized_base_folder": str(paths["organized"]),
            "destination_folders": {
                "documents": str(paths["documents"]),
                "others": str(paths["organized"] / "others"),
            },
            "rules": {"documents": [".txt"]},
            "processing_wait_seconds": 0,
            "duplicate_event_window_seconds": 0,
            "archive_by_date": False,
            "stats_file": str(paths["stats"]),
            "history_file": str(paths["history"]),
            "hash_db_file": str(paths["hashes"]),
        },
        {".txt": "documents"},
    )
    submitted = []
    monitor.submit = lambda path, event: submitted.append(
        {"path": str(Path(path)), "event": event}
    )
    monitor.scan_existing_files()
    (root / "scan-result.json").write_text(
        json.dumps(submitted),
        encoding="utf-8",
    )


def _reprocess_cross_volume(root: Path) -> None:
    with _force_cross_volume(root, "reprocess_cross"):
        result = _move(root)
    (root / "reprocess-result.json").write_text(
        json.dumps(
            {
                "status": result.status.value,
                "destination": str(result.destination) if result.destination else None,
                "error": result.error,
            }
        ),
        encoding="utf-8",
    )


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: recovery_crash_worker.py MODE ROOT")
    mode = sys.argv[1]
    root = Path(sys.argv[2]).resolve()
    if mode in EXIT_CODES:
        _run_crash_scenario(root, mode)
    elif mode == "scan_startup":
        _scan_startup(root)
    elif mode == "reprocess_cross":
        _reprocess_cross_volume(root)
    else:
        raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()
