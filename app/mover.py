import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath

from app.classifier import get_file_category
from app.stats import update_stats, append_history
from app.hash_manager import (
    calculate_file_hash,
    get_verified_file_path,
    hash_operation,
    register_file_hash,
)


class MoveStatus(Enum):
    MOVED = "moved"
    DUPLICATE = "duplicate"
    HASH_CHECK_FAILED = "hash_check_failed"
    MOVE_FAILED = "failed"


@dataclass(frozen=True)
class MoveResult:
    status: MoveStatus
    source: Path
    destination: Path | None = None
    duplicate_of: Path | None = None
    error: str | None = None


class _SourceRemovalError(OSError):
    def __init__(self, source: Path, destination: Path, error: OSError):
        super().__init__(
            f"Published {destination} but could not remove source {source}: {error}"
        )
        self.destination = destination


class UnsafeDestinationError(ValueError):
    pass


def validate_category(category: object) -> str:
    """Reject category values that can alter the organized destination root."""
    if not isinstance(category, str) or not category:
        raise UnsafeDestinationError("Category must be a non-empty string")

    windows_path = PureWindowsPath(category)
    posix_path = PurePosixPath(category)
    path_parts = category.replace("\\", "/").split("/")
    if (
        windows_path.drive
        or windows_path.root
        or posix_path.is_absolute()
        or any(part in {".", ".."} for part in path_parts)
    ):
        raise UnsafeDestinationError(f"Unsafe category path: {category!r}")

    return category


def resolve_contained_path(organized_root: Path, destination: Path) -> Path:
    """Return a canonical destination only when it is within organized_root."""
    resolved_root = organized_root.resolve(strict=False)
    resolved_destination = destination.resolve(strict=False)
    if not resolved_destination.is_relative_to(resolved_root):
        raise UnsafeDestinationError(
            f"Destination escapes organized root: {resolved_destination}"
        )
    return resolved_destination


def generate_unique_destination(destination_path: Path) -> Path:
    """Generate a unique name if the file already exists at the destination."""
    if not os.path.lexists(destination_path):
        return destination_path

    stem = destination_path.stem
    suffix = destination_path.suffix
    parent = destination_path.parent

    counter = 1
    while True:
        new_name = f"{stem}({counter}){suffix}"
        new_path = parent / new_name
        if not os.path.lexists(new_path):
            return new_path
        counter += 1


def _move_no_clobber(source_file: Path, destination_file: Path) -> Path:
    """Move one file without ever replacing an existing destination."""
    if os.name == "nt":
        os.rename(source_file, destination_file)
        return destination_file

    os.link(source_file, destination_file, follow_symlinks=False)
    try:
        os.unlink(source_file)
    except OSError as error:
        raise _SourceRemovalError(source_file, destination_file, error) from error
    return destination_file


def _move_to_unique_destination(
    source_file: Path,
    destination_file: Path,
    organized_root: Path,
) -> Path:
    """Select and atomically commit to an available destination path."""
    while True:
        candidate = generate_unique_destination(destination_file)
        candidate = resolve_contained_path(organized_root, candidate)
        try:
            return _move_no_clobber(source_file, candidate)
        except FileExistsError:
            logging.debug(
                f"Destination collision while moving {source_file.name}: {candidate}"
            )


def get_dated_destination_dir(base_dir: Path, archive_by_date: bool) -> Path:
    """
    If date-based archiving is enabled, add a date subfolder like 2026-03.
    """
    if not archive_by_date:
        return base_dir
    date_folder = datetime.now().strftime("%Y-%m")
    return base_dir / date_folder


def move_file_with_retries(
    source_file: Path,
    destination_folders: dict,
    extension_lookup: dict,
    stats_file: str,
    history_file: str,
    hash_db_file: str,
    archive_by_date: bool,
    rules: dict,
    organized_root: str | Path,
    retries: int = 8,
    delay: int = 2,
    classification_method: str = "extension",
    smart_source: str = "",
    category_override: str | None = None,
) -> MoveResult:
    """Attempt to move the file with retries, hash-based duplicate check, and date archiving."""
    category = (
        category_override
        if category_override is not None
        else get_file_category(source_file, extension_lookup)
    )

    logging.debug(f"Processing: {source_file.name} | suffix: {source_file.suffix!r} | category: {category}")

    if not source_file.exists():
        logging.warning(f"File no longer exists before hashing: {source_file}")
        append_history(history_file, source_file.name, category, "disappeared")
        return MoveResult(
            MoveStatus.MOVE_FAILED,
            source_file,
            error="Source file disappeared before hashing",
        )

    try:
        category = validate_category(category)
        organized_root_path = Path(organized_root).resolve(strict=False)
        if category not in destination_folders:
            category = "others"
        base_destination_dir = Path(destination_folders[category])
        destination_dir = get_dated_destination_dir(
            base_destination_dir,
            archive_by_date,
        )
        destination_file = resolve_contained_path(
            organized_root_path,
            destination_dir / source_file.name,
        )
    except (
        KeyError,
        OSError,
        RuntimeError,
        TypeError,
        UnsafeDestinationError,
    ) as error:
        logging.error(f"Rejected unsafe destination for {source_file.name}: {error}")
        update_stats(stats_file, category, rules, success=False)
        append_history(
            history_file,
            source_file.name,
            category,
            "failed",
            classification_method,
            smart_source,
        )
        return MoveResult(MoveStatus.MOVE_FAILED, source_file, error=str(error))

    try:
        file_hash = calculate_file_hash(source_file)
    except Exception as error:
        logging.error(f"Hash check failed for {source_file.name}: {error}")
        append_history(history_file, source_file.name, category, "hash_check_failed")
        return MoveResult(MoveStatus.HASH_CHECK_FAILED, source_file, error=str(error))

    last_error = None
    result: MoveResult | None = None
    failure_history_status = "failed"

    try:
        with hash_operation(file_hash):
            existing_path = get_verified_file_path(file_hash, hash_db_file)
            if existing_path is not None:
                result = MoveResult(
                    MoveStatus.DUPLICATE,
                    source_file,
                    duplicate_of=Path(existing_path),
                )
            else:
                try:
                    destination_file.parent.mkdir(parents=True, exist_ok=True)
                except Exception as error:
                    last_error = error
                    result = MoveResult(
                        MoveStatus.MOVE_FAILED,
                        source_file,
                        error=str(error),
                    )
                else:
                    for attempt in range(1, retries + 1):
                        if not source_file.exists():
                            last_error = FileNotFoundError(
                                f"Source file disappeared before move: {source_file}"
                            )
                            failure_history_status = "disappeared"
                            logging.warning(
                                f"File disappeared before move (attempt {attempt}): {source_file}"
                            )
                            result = MoveResult(
                                MoveStatus.MOVE_FAILED,
                                source_file,
                                error=str(last_error),
                            )
                            break

                        try:
                            actual_destination = _move_to_unique_destination(
                                source_file,
                                destination_file,
                                organized_root_path,
                            )
                        except UnsafeDestinationError as error:
                            last_error = error
                            result = MoveResult(
                                MoveStatus.MOVE_FAILED,
                                source_file,
                                error=str(error),
                            )
                            break
                        except _SourceRemovalError as error:
                            last_error = error
                            result = MoveResult(
                                MoveStatus.MOVE_FAILED,
                                source_file,
                                destination=error.destination,
                                error=str(error),
                            )
                            break
                        except PermissionError as error:
                            last_error = error
                            logging.debug(
                                f"PermissionError (attempt {attempt}/{retries}): {source_file.name}"
                            )
                        except OSError as error:
                            last_error = error
                            logging.debug(
                                f"OSError (attempt {attempt}/{retries}): {source_file.name}"
                            )
                        except Exception as error:
                            last_error = error
                            logging.debug(
                                f"Unexpected error (attempt {attempt}/{retries}): {source_file.name}"
                            )
                        else:
                            try:
                                register_file_hash(
                                    file_hash,
                                    str(actual_destination),
                                    hash_db_file,
                                )
                            except Exception as error:
                                last_error = error
                                result = MoveResult(
                                    MoveStatus.MOVE_FAILED,
                                    source_file,
                                    destination=actual_destination,
                                    error=str(error),
                                )
                            else:
                                result = MoveResult(
                                    MoveStatus.MOVED,
                                    source_file,
                                    destination=actual_destination,
                                )
                            break

                        if attempt < retries:
                            time.sleep(delay)

                    if result is None:
                        result = MoveResult(
                            MoveStatus.MOVE_FAILED,
                            source_file,
                            error=str(last_error) if last_error is not None else "No move attempts were made",
                        )
    except Exception as error:
        last_error = error
        result = MoveResult(MoveStatus.MOVE_FAILED, source_file, error=str(error))

    if result.status is MoveStatus.DUPLICATE:
        logging.info(
            f"Duplicate retained: {source_file.name} | category: {category} | existing: {result.duplicate_of}"
        )
        append_history(
            history_file,
            source_file.name,
            category,
            "duplicate_skipped",
            classification_method,
            smart_source,
        )
        return result

    if result.status is MoveStatus.MOVED:
        append_history(
            history_file,
            source_file.name,
            category,
            "moved",
            classification_method,
            smart_source,
        )
        update_stats(stats_file, category, rules, success=True)
        logging.info(
            f"Moved: {source_file.name} → {result.destination} | category: {category} | method: {classification_method}"
        )
        return result

    logging.error(
        f"Failed to move after {retries} retries: {source_file.name} | error: {last_error}"
    )
    if failure_history_status == "failed":
        update_stats(stats_file, category, rules, success=False)
    append_history(history_file, source_file.name, category, failure_history_status)
    return result
