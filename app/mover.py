import errno
import hashlib
import logging
import os
import tempfile
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
    metadata_error: str | None = None


class _SourceRemovalError(OSError):
    def __init__(self, source: Path, destination: Path, error: OSError):
        super().__init__(
            f"Published {destination} but could not remove source {source}: {error}"
        )
        self.destination = destination


class UnsafeDestinationError(ValueError):
    pass


@dataclass(frozen=True)
class _FileState:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class _TemporaryCopy:
    path: Path
    state: _FileState
    source_state: _FileState


_WINDOWS_ERROR_NOT_SAME_DEVICE = 17
_MAX_CROSS_VOLUME_COLLISIONS = 1000
_COPY_CHUNK_SIZE = 1024 * 1024


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


def _file_state(stat_result: os.stat_result) -> _FileState:
    return _FileState(
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size=stat_result.st_size,
        modified_ns=stat_result.st_mtime_ns,
        changed_ns=stat_result.st_ctime_ns,
    )


def _is_cross_device_error(error: OSError) -> bool:
    return (
        error.errno == errno.EXDEV
        or getattr(error, "winerror", None) == _WINDOWS_ERROR_NOT_SAME_DEVICE
    )


def _same_file_identity(first: _FileState, second: _FileState) -> bool:
    return first.device == second.device and first.inode == second.inode


def _same_source_version(first: _FileState, second: _FileState) -> bool:
    return (
        _same_file_identity(first, second)
        and first.size == second.size
        and first.modified_ns == second.modified_ns
    )


def _cleanup_temporary_copy(temporary: _TemporaryCopy) -> None:
    """Remove only the exact private temporary file created by this operation."""
    try:
        current_state = _file_state(temporary.path.stat(follow_symlinks=False))
        if not _same_file_identity(current_state, temporary.state):
            logging.warning(
                f"Temporary path changed before cleanup; preserving it: {temporary.path}"
            )
            return
        os.unlink(temporary.path)
    except FileNotFoundError:
        return
    except OSError as error:
        logging.warning(
            f"Could not clean up temporary copy {temporary.path}: {error}"
        )


def _copy_file_data(source, destination) -> str:
    copied_hash = hashlib.sha256()
    while chunk := source.read(_COPY_CHUNK_SIZE):
        destination.write(chunk)
        copied_hash.update(chunk)
    return copied_hash.hexdigest()


def _copy_to_verified_temporary(
    source_file: Path,
    destination_dir: Path,
    organized_root: Path,
    expected_hash: str,
    expected_source_state: _FileState | None,
) -> _TemporaryCopy:
    """Copy source into a private destination-side file and verify its bytes."""
    destination_dir = resolve_contained_path(organized_root, destination_dir)
    descriptor = -1
    temporary: _TemporaryCopy | None = None
    temporary_path: Path | None = None
    temporary_state: _FileState | None = None

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".filepilot-",
            suffix=".tmp",
            dir=destination_dir,
        )
        temporary_path = Path(temporary_name)
        temporary_state = _file_state(os.fstat(descriptor))
        temporary_path = resolve_contained_path(
            organized_root,
            temporary_path,
        )

        with open(source_file, "rb") as source, os.fdopen(
            descriptor,
            "wb",
        ) as destination:
            descriptor = -1
            source_state = _file_state(os.fstat(source.fileno()))
            if expected_source_state is None or not _same_source_version(
                source_state,
                expected_source_state,
            ):
                raise OSError(f"Source changed before cross-volume copy: {source_file}")
            copied_hash = _copy_file_data(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
            if _file_state(os.fstat(source.fileno())) != source_state:
                raise OSError(f"Source changed while copying: {source_file}")

        current_temporary_state = _file_state(
            temporary_path.stat(follow_symlinks=False)
        )
        temporary = _TemporaryCopy(
            temporary_path,
            temporary_state,
            source_state,
        )
        if not _same_file_identity(current_temporary_state, temporary_state):
            raise OSError(f"Temporary file changed while copying: {temporary_path}")
        if copied_hash != expected_hash:
            raise OSError(f"Source content changed before cross-volume copy: {source_file}")
        if calculate_file_hash(temporary_path) != expected_hash:
            raise OSError(f"Temporary copy verification failed: {temporary_path}")
        return temporary
    except Exception:
        if descriptor != -1:
            os.close(descriptor)
        if (
            temporary is None
            and temporary_path is not None
            and temporary_state is not None
        ):
            temporary = _TemporaryCopy(
                temporary_path,
                temporary_state,
                _FileState(0, 0, 0, 0, 0),
            )
        if temporary is not None:
            _cleanup_temporary_copy(temporary)
        raise


def _publish_temporary_no_clobber(
    temporary: _TemporaryCopy,
    destination_file: Path,
    organized_root: Path,
) -> Path:
    """Atomically publish a verified temporary file without replacing collisions."""
    for _ in range(_MAX_CROSS_VOLUME_COLLISIONS):
        candidate = generate_unique_destination(destination_file)
        candidate = resolve_contained_path(organized_root, candidate)
        try:
            return _move_no_clobber(temporary.path, candidate)
        except FileExistsError:
            logging.debug(
                f"Destination collision while publishing {destination_file.name}: "
                f"{candidate}"
            )
        except _SourceRemovalError as error:
            # The no-clobber link is already the committed destination. Cleanup of
            # the private temporary name is best effort and must not undo it.
            _cleanup_temporary_copy(temporary)
            return error.destination
    raise FileExistsError(
        f"Could not select a destination after "
        f"{_MAX_CROSS_VOLUME_COLLISIONS} collisions: {destination_file}"
    )


def _remove_verified_source(
    source_file: Path,
    destination_file: Path,
    expected_hash: str,
    expected_state: _FileState,
) -> None:
    """Atomically isolate and delete only the verified source instance."""
    staging_dir: Path | None = None
    staged_source: Path | None = None
    source_staged = False

    def restore_staged_source() -> str | None:
        if (
            not source_staged
            or staged_source is None
            or not os.path.lexists(staged_source)
        ):
            return None
        try:
            _move_no_clobber(staged_source, source_file)
            return None
        except _SourceRemovalError:
            # The source name was restored; only the private extra link remains.
            return f"source restored but private staged link remains at {staged_source}"
        except OSError as restore_error:
            return f"verified source remains at {staged_source}: {restore_error}"

    try:
        before_hash_state = _file_state(source_file.stat())
        if not _same_source_version(before_hash_state, expected_state):
            raise OSError(f"Source changed before removal: {source_file}")
        if calculate_file_hash(source_file) != expected_hash:
            raise OSError(f"Source content changed before removal: {source_file}")
        if not _same_source_version(_file_state(source_file.stat()), expected_state):
            raise OSError(f"Source changed during removal verification: {source_file}")

        staging_dir = Path(
            tempfile.mkdtemp(prefix=".filepilot-remove-", dir=source_file.parent)
        )
        staged_source = staging_dir / source_file.name
        os.rename(source_file, staged_source)
        source_staged = True

        staged_state = _file_state(staged_source.stat())
        if not _same_source_version(staged_state, expected_state):
            raise OSError(f"Source was replaced before removal: {source_file}")
        if calculate_file_hash(staged_source) != expected_hash:
            raise OSError(f"Staged source content did not match: {source_file}")
        if not _same_source_version(
            _file_state(staged_source.stat()),
            expected_state,
        ):
            raise OSError(f"Staged source changed before removal: {source_file}")
        if calculate_file_hash(destination_file) != expected_hash:
            raise OSError(
                f"Published destination changed before source removal: "
                f"{destination_file}"
            )
        os.unlink(staged_source)
        source_staged = False
    except OSError as error:
        restore_error = restore_staged_source()
        if restore_error is not None:
            error = OSError(f"{error}; {restore_error}")
        raise _SourceRemovalError(source_file, destination_file, error) from error
    finally:
        if staging_dir is not None:
            try:
                os.rmdir(staging_dir)
            except OSError as error:
                logging.warning(
                    f"Could not clean up source staging directory {staging_dir}: {error}"
                )


def _move_across_volumes(
    source_file: Path,
    destination_file: Path,
    organized_root: Path,
    expected_hash: str,
    expected_source_state: _FileState | None,
) -> Path:
    """Copy, verify, publish, and only then remove a cross-volume source."""
    if source_file.is_symlink():
        raise OSError(f"Cross-volume symlink sources are not supported: {source_file}")
    temporary = _copy_to_verified_temporary(
        source_file,
        destination_file.parent,
        organized_root,
        expected_hash,
        expected_source_state,
    )
    published_destination: Path | None = None
    try:
        published_destination = _publish_temporary_no_clobber(
            temporary,
            destination_file,
            organized_root,
        )
        try:
            published_hash = calculate_file_hash(published_destination)
        except OSError as error:
            raise _SourceRemovalError(
                source_file,
                published_destination,
                OSError(f"Published destination verification failed: {error}"),
            ) from error
        if published_hash != expected_hash:
            raise _SourceRemovalError(
                source_file,
                published_destination,
                OSError(
                    f"Published destination content did not match source: "
                    f"{published_destination}"
                ),
            )
        _remove_verified_source(
            source_file,
            published_destination,
            expected_hash,
            temporary.source_state,
        )
        return published_destination
    finally:
        _cleanup_temporary_copy(temporary)


def _move_to_unique_destination(
    source_file: Path,
    destination_file: Path,
    organized_root: Path,
    expected_hash: str,
    expected_source_state: _FileState | None,
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
        except OSError as error:
            if not _is_cross_device_error(error):
                raise
            logging.debug(
                f"Cross-volume move detected for {source_file}; using verified copy"
            )
            return _move_across_volumes(
                source_file,
                destination_file,
                organized_root,
                expected_hash,
                expected_source_state,
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
        pre_hash_source_state = _file_state(source_file.stat())
    except OSError:
        pre_hash_source_state = None

    try:
        file_hash = calculate_file_hash(source_file)
    except Exception as error:
        logging.error(f"Hash check failed for {source_file.name}: {error}")
        append_history(history_file, source_file.name, category, "hash_check_failed")
        return MoveResult(MoveStatus.HASH_CHECK_FAILED, source_file, error=str(error))

    try:
        post_hash_source_state = _file_state(source_file.stat())
    except OSError:
        hashed_source_state = None
    else:
        if (
            pre_hash_source_state is not None
            and _same_source_version(
                pre_hash_source_state,
                post_hash_source_state,
            )
        ):
            hashed_source_state = post_hash_source_state
        else:
            # Same-volume rename remains safe; cross-volume fallback will refuse
            # to delete a source whose hash was not bound to one file instance.
            hashed_source_state = None

    last_error = None
    result: MoveResult | None = None
    failure_history_status = "failed"
    committed_destination: Path | None = None
    metadata_errors: list[str] = []

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
                                file_hash,
                                hashed_source_state,
                            )
                            committed_destination = actual_destination
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
                                committed_hash = calculate_file_hash(
                                    actual_destination
                                )
                            except Exception as error:
                                message = (
                                    "Committed file hash calculation failed: "
                                    f"{error}"
                                )
                                metadata_errors.append(message)
                                logging.error(
                                    f"{message} | destination: {actual_destination}"
                                )
                            else:
                                if committed_hash != file_hash:
                                    logging.warning(
                                        "Source content changed before commit: "
                                        f"{source_file.name} | original hash: {file_hash} "
                                        f"| committed hash: {committed_hash}"
                                    )
                                try:
                                    register_file_hash(
                                        committed_hash,
                                        str(actual_destination),
                                        hash_db_file,
                                    )
                                except Exception as error:
                                    message = f"Hash registration failed: {error}"
                                    metadata_errors.append(message)
                                    logging.error(
                                        f"{message} | destination: {actual_destination}"
                                    )
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
        if committed_destination is not None:
            message = f"Post-commit processing failed: {error}"
            metadata_errors.append(message)
            logging.error(
                f"{message} | destination: {committed_destination}"
            )
            result = MoveResult(
                MoveStatus.MOVED,
                source_file,
                destination=committed_destination,
            )
        else:
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
        try:
            append_history(
                history_file,
                source_file.name,
                category,
                "moved",
                classification_method,
                smart_source,
            )
        except Exception as error:
            message = f"History update failed: {error}"
            metadata_errors.append(message)
            logging.error(f"{message} | destination: {result.destination}")

        try:
            update_stats(stats_file, category, rules, success=True)
        except Exception as error:
            message = f"Stats update failed: {error}"
            metadata_errors.append(message)
            logging.error(f"{message} | destination: {result.destination}")

        if metadata_errors:
            result = MoveResult(
                MoveStatus.MOVED,
                source_file,
                destination=result.destination,
                metadata_error="; ".join(metadata_errors),
            )
            logging.warning(
                f"Moved with metadata errors: {source_file.name} → {result.destination} "
                f"| errors: {result.metadata_error}"
            )
        else:
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
