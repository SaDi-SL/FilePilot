import os
import re
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Iterator, Mapping


# "FPJ1" encoded as a stable positive 32-bit SQLite application identifier.
APPLICATION_ID = 0x46504A31
SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 5000
_MAX_ERROR_MESSAGE_LENGTH = 512
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z0-9_.-]{1,64}$")


class OperationType(str, Enum):
    MOVE = "move"


class PhysicalPhase(str, Enum):
    PREPARED = "prepared"
    RENAME_INTENT = "rename_intent"
    TEMP_CREATE_INTENT = "temp_create_intent"
    TEMP_CREATED = "temp_created"
    TEMP_VERIFIED = "temp_verified"
    PUBLISH_INTENT = "publish_intent"
    DESTINATION_PUBLISHED = "destination_published"
    DESTINATION_VERIFIED = "destination_verified"
    SOURCE_STAGE_INTENT = "source_stage_intent"
    SOURCE_STAGED = "source_staged"
    SOURCE_DELETE_INTENT = "source_delete_intent"
    PHYSICAL_COMMITTED = "physical_committed"
    ABORTED = "aborted"
    NEEDS_REVIEW = "needs_review"


class OperationStatus(str, Enum):
    OPEN = "open"
    DUPLICATE = "duplicate"
    ABORTED = "aborted"
    NEEDS_REVIEW = "needs_review"
    COMPLETE = "complete"


class SourceObjectType(str, Enum):
    FILE = "file"
    SYMLINK = "symlink"
    OTHER = "other"


class MoveMode(str, Enum):
    UNKNOWN = "unknown"
    SAME_VOLUME = "same_volume"
    CROSS_VOLUME = "cross_volume"


class EventKind(str, Enum):
    OPERATION_CREATED = "operation_created"
    PHASE_TRANSITION = "phase_transition"
    OUTCOME_RECORDED = "outcome_recorded"
    EFFECT_INITIALIZED = "effect_initialized"
    EFFECT_TRANSITION = "effect_transition"


class EffectType(str, Enum):
    HASH_INDEX = "hash_index"
    DURABLE_HISTORY = "durable_history"
    LEGACY_CSV_HISTORY = "legacy_csv_history"
    LEGACY_STATISTICS = "legacy_statistics"
    NOTIFICATION_OUTBOX = "notification_outbox"


class EffectState(str, Enum):
    NOT_STARTED = "not_started"
    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"
    UNKNOWN = "unknown"


class JournalError(Exception):
    pass


class JournalPathError(JournalError):
    pass


class JournalDatabaseError(JournalError):
    pass


class JournalCorruptionError(JournalDatabaseError):
    pass


class JournalIncompatibleError(JournalDatabaseError):
    pass


class JournalSchemaError(JournalIncompatibleError):
    pass


class JournalValidationError(JournalError, ValueError):
    pass


class JournalConflictError(JournalError):
    pass


class JournalNotFoundError(JournalError, LookupError):
    pass


@dataclass(frozen=True)
class TransitionEvidence:
    actual_destination: str | Path | None = None
    destination_identity: str | None = None
    destination_hash: str | None = None
    destination_size: int | None = None
    temp_path: str | Path | None = None
    temp_identity: str | None = None
    staging_path: str | Path | None = None
    staging_identity: str | None = None


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    operation_type: OperationType
    physical_phase: PhysicalPhase
    operation_status: OperationStatus
    source_path: str
    source_identity: str | None
    source_hash: str | None
    source_size: int | None
    source_mtime_ns: int | None
    source_object_type: SourceObjectType
    organized_root: str
    intended_destination: str
    actual_destination: str | None
    duplicate_of_path: str | None
    destination_identity: str | None
    destination_hash: str | None
    destination_size: int | None
    temp_path: str | None
    temp_identity: str | None
    staging_path: str | None
    staging_identity: str | None
    move_mode: MoveMode
    started_at_utc: str
    updated_at_utc: str
    published_at_utc: str | None
    physically_committed_at_utc: str | None
    completed_at_utc: str | None
    parent_operation_id: str | None
    inverse_of_operation_id: str | None
    error_code: str | None
    error_message: str | None
    application_version: str | None


@dataclass(frozen=True)
class OperationEvent:
    event_id: int
    operation_id: str
    sequence_number: int
    event_kind: EventKind
    from_phase: PhysicalPhase | None
    to_phase: PhysicalPhase | None
    from_status: OperationStatus | None
    to_status: OperationStatus | None
    effect_type: EffectType | None
    from_effect_state: EffectState | None
    to_effect_state: EffectState | None
    error_code: str | None
    error_message: str | None
    occurred_at_utc: str


@dataclass(frozen=True)
class OperationEffect:
    operation_id: str
    effect_type: EffectType
    state: EffectState
    required: bool
    updated_at_utc: str
    error_code: str | None
    error_message: str | None


def resolve_default_journal_path(
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Return FilePilot's machine-local per-user operation journal path."""
    environment = os.environ if environment is None else environment
    local_app_data = environment.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise JournalPathError(
            "LOCALAPPDATA is unavailable; an explicit safe journal path is required"
        )

    if _is_network_path(local_app_data):
        raise JournalPathError("The operation journal cannot use a network path")

    base_path = Path(local_app_data).expanduser()
    if not base_path.is_absolute():
        raise JournalPathError("LOCALAPPDATA must resolve to an absolute path")
    return base_path / "FilePilot" / "data" / "operations.sqlite3"


def _is_network_path(value: str | Path) -> bool:
    windows_path = PureWindowsPath(str(value))
    if windows_path.anchor.startswith("\\\\"):
        return True
    if not windows_path.drive:
        return False
    return _windows_drive_type(f"{windows_path.drive}\\") == 4


def _windows_drive_type(root: str) -> int | None:
    if os.name != "nt":
        return None
    import ctypes

    return ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root))


def _enum_sql_values(enum_type: type[Enum]) -> str:
    return ", ".join(f"'{member.value}'" for member in enum_type)


_OPERATION_TYPES = _enum_sql_values(OperationType)
_PHYSICAL_PHASES = _enum_sql_values(PhysicalPhase)
_OPERATION_STATUSES = _enum_sql_values(OperationStatus)
_SOURCE_OBJECT_TYPES = _enum_sql_values(SourceObjectType)
_MOVE_MODES = _enum_sql_values(MoveMode)
_EVENT_KINDS = _enum_sql_values(EventKind)
_EFFECT_TYPES = _enum_sql_values(EffectType)
_EFFECT_STATES = _enum_sql_values(EffectState)


_SCHEMA_STATEMENTS = (
    f"""
    CREATE TABLE operations (
        operation_id TEXT PRIMARY KEY,
        operation_type TEXT NOT NULL CHECK (operation_type IN ({_OPERATION_TYPES})),
        physical_phase TEXT NOT NULL CHECK (physical_phase IN ({_PHYSICAL_PHASES})),
        operation_status TEXT NOT NULL CHECK (operation_status IN ({_OPERATION_STATUSES})),
        source_path TEXT NOT NULL,
        source_identity TEXT,
        source_hash TEXT,
        source_size INTEGER CHECK (source_size IS NULL OR source_size >= 0),
        source_mtime_ns INTEGER CHECK (source_mtime_ns IS NULL OR source_mtime_ns >= 0),
        source_object_type TEXT NOT NULL CHECK (source_object_type IN ({_SOURCE_OBJECT_TYPES})),
        organized_root TEXT NOT NULL,
        intended_destination TEXT NOT NULL,
        actual_destination TEXT,
        duplicate_of_path TEXT,
        destination_identity TEXT,
        destination_hash TEXT,
        destination_size INTEGER CHECK (destination_size IS NULL OR destination_size >= 0),
        temp_path TEXT,
        temp_identity TEXT,
        staging_path TEXT,
        staging_identity TEXT,
        move_mode TEXT NOT NULL CHECK (move_mode IN ({_MOVE_MODES})),
        started_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        published_at_utc TEXT,
        physically_committed_at_utc TEXT,
        completed_at_utc TEXT,
        parent_operation_id TEXT REFERENCES operations(operation_id),
        inverse_of_operation_id TEXT REFERENCES operations(operation_id),
        error_code TEXT,
        error_message TEXT,
        application_version TEXT,
        CHECK (parent_operation_id IS NULL OR parent_operation_id != operation_id),
        CHECK (inverse_of_operation_id IS NULL OR inverse_of_operation_id != operation_id)
    )
    """,
    f"""
    CREATE TABLE operation_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        operation_id TEXT NOT NULL REFERENCES operations(operation_id) ON DELETE CASCADE,
        sequence_number INTEGER NOT NULL CHECK (sequence_number > 0),
        event_kind TEXT NOT NULL CHECK (event_kind IN ({_EVENT_KINDS})),
        from_phase TEXT CHECK (from_phase IN ({_PHYSICAL_PHASES})),
        to_phase TEXT CHECK (to_phase IN ({_PHYSICAL_PHASES})),
        from_status TEXT CHECK (from_status IN ({_OPERATION_STATUSES})),
        to_status TEXT CHECK (to_status IN ({_OPERATION_STATUSES})),
        effect_type TEXT CHECK (effect_type IN ({_EFFECT_TYPES})),
        from_effect_state TEXT CHECK (from_effect_state IN ({_EFFECT_STATES})),
        to_effect_state TEXT CHECK (to_effect_state IN ({_EFFECT_STATES})),
        error_code TEXT,
        error_message TEXT,
        occurred_at_utc TEXT NOT NULL
    )
    """,
    f"""
    CREATE TABLE operation_effects (
        operation_id TEXT NOT NULL REFERENCES operations(operation_id) ON DELETE CASCADE,
        effect_type TEXT NOT NULL CHECK (effect_type IN ({_EFFECT_TYPES})),
        state TEXT NOT NULL CHECK (state IN ({_EFFECT_STATES})),
        required INTEGER NOT NULL CHECK (required IN (0, 1)),
        updated_at_utc TEXT NOT NULL,
        error_code TEXT,
        error_message TEXT,
        PRIMARY KEY (operation_id, effect_type)
    )
    """,
    """
    CREATE INDEX idx_operations_status_updated
    ON operations(operation_status, updated_at_utc)
    """,
    """
    CREATE INDEX idx_operations_parent
    ON operations(parent_operation_id)
    """,
    """
    CREATE INDEX idx_operations_inverse
    ON operations(inverse_of_operation_id)
    """,
    """
    CREATE UNIQUE INDEX idx_operation_events_operation_sequence
    ON operation_events(operation_id, sequence_number)
    """,
    """
    CREATE INDEX idx_operation_effects_state
    ON operation_effects(state, updated_at_utc)
    """,
)


_EXPECTED_COLUMNS = {
    "operations": {
        "operation_id",
        "operation_type",
        "physical_phase",
        "operation_status",
        "source_path",
        "source_identity",
        "source_hash",
        "source_size",
        "source_mtime_ns",
        "source_object_type",
        "organized_root",
        "intended_destination",
        "actual_destination",
        "duplicate_of_path",
        "destination_identity",
        "destination_hash",
        "destination_size",
        "temp_path",
        "temp_identity",
        "staging_path",
        "staging_identity",
        "move_mode",
        "started_at_utc",
        "updated_at_utc",
        "published_at_utc",
        "physically_committed_at_utc",
        "completed_at_utc",
        "parent_operation_id",
        "inverse_of_operation_id",
        "error_code",
        "error_message",
        "application_version",
    },
    "operation_events": {
        "event_id",
        "operation_id",
        "sequence_number",
        "event_kind",
        "from_phase",
        "to_phase",
        "from_status",
        "to_status",
        "effect_type",
        "from_effect_state",
        "to_effect_state",
        "error_code",
        "error_message",
        "occurred_at_utc",
    },
    "operation_effects": {
        "operation_id",
        "effect_type",
        "state",
        "required",
        "updated_at_utc",
        "error_code",
        "error_message",
    },
}


_EXPECTED_TABLE_SQL = {
    "operations": _SCHEMA_STATEMENTS[0],
    "operation_events": _SCHEMA_STATEMENTS[1],
    "operation_effects": _SCHEMA_STATEMENTS[2],
}


_EXPECTED_INDEXES = {
    "idx_operations_status_updated": (
        "operations",
        False,
        ("operation_status", "updated_at_utc"),
    ),
    "idx_operations_parent": ("operations", False, ("parent_operation_id",)),
    "idx_operations_inverse": ("operations", False, ("inverse_of_operation_id",)),
    "idx_operation_events_operation_sequence": (
        "operation_events",
        True,
        ("operation_id", "sequence_number"),
    ),
    "idx_operation_effects_state": (
        "operation_effects",
        False,
        ("state", "updated_at_utc"),
    ),
}


_PHASE_TRANSITIONS = {
    PhysicalPhase.PREPARED: {
        PhysicalPhase.RENAME_INTENT,
        PhysicalPhase.TEMP_CREATE_INTENT,
        PhysicalPhase.ABORTED,
        PhysicalPhase.NEEDS_REVIEW,
    },
    PhysicalPhase.RENAME_INTENT: {
        PhysicalPhase.RENAME_INTENT,
        PhysicalPhase.TEMP_CREATE_INTENT,
        PhysicalPhase.PHYSICAL_COMMITTED,
        PhysicalPhase.ABORTED,
        PhysicalPhase.NEEDS_REVIEW,
    },
    PhysicalPhase.TEMP_CREATE_INTENT: {
        PhysicalPhase.TEMP_CREATED,
        PhysicalPhase.ABORTED,
        PhysicalPhase.NEEDS_REVIEW,
    },
    PhysicalPhase.TEMP_CREATED: {
        PhysicalPhase.TEMP_VERIFIED,
        PhysicalPhase.ABORTED,
        PhysicalPhase.NEEDS_REVIEW,
    },
    PhysicalPhase.TEMP_VERIFIED: {
        PhysicalPhase.PUBLISH_INTENT,
        PhysicalPhase.ABORTED,
        PhysicalPhase.NEEDS_REVIEW,
    },
    PhysicalPhase.PUBLISH_INTENT: {
        PhysicalPhase.PUBLISH_INTENT,
        PhysicalPhase.DESTINATION_PUBLISHED,
        PhysicalPhase.ABORTED,
        PhysicalPhase.NEEDS_REVIEW,
    },
    PhysicalPhase.DESTINATION_PUBLISHED: {
        PhysicalPhase.DESTINATION_VERIFIED,
        PhysicalPhase.NEEDS_REVIEW,
    },
    PhysicalPhase.DESTINATION_VERIFIED: {
        PhysicalPhase.SOURCE_STAGE_INTENT,
        PhysicalPhase.NEEDS_REVIEW,
    },
    PhysicalPhase.SOURCE_STAGE_INTENT: {
        PhysicalPhase.SOURCE_STAGED,
        PhysicalPhase.NEEDS_REVIEW,
    },
    PhysicalPhase.SOURCE_STAGED: {
        PhysicalPhase.SOURCE_DELETE_INTENT,
        PhysicalPhase.NEEDS_REVIEW,
    },
    PhysicalPhase.SOURCE_DELETE_INTENT: {
        PhysicalPhase.PHYSICAL_COMMITTED,
        PhysicalPhase.NEEDS_REVIEW,
    },
    PhysicalPhase.PHYSICAL_COMMITTED: set(),
    PhysicalPhase.ABORTED: set(),
    PhysicalPhase.NEEDS_REVIEW: set(),
}


_EFFECT_TRANSITIONS = {
    EffectState.NOT_STARTED: {EffectState.PENDING},
    EffectState.PENDING: {
        EffectState.APPLIED,
        EffectState.FAILED,
        EffectState.UNKNOWN,
    },
    EffectState.FAILED: {EffectState.PENDING, EffectState.UNKNOWN},
    EffectState.UNKNOWN: {
        EffectState.PENDING,
        EffectState.APPLIED,
        EffectState.FAILED,
    },
    EffectState.APPLIED: set(),
}


def _migrate_to_v1(connection: sqlite3.Connection) -> None:
    for statement in _SCHEMA_STATEMENTS:
        connection.execute(statement)


_MIGRATIONS = {1: _migrate_to_v1}


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _normalize_schema_sql(value: str) -> str:
    return " ".join(value.split())


def _required_enum(value, enum_type, field_name):
    if not isinstance(value, enum_type):
        raise JournalValidationError(
            f"{field_name} must be a {enum_type.__name__} value"
        )
    return value


def _bounded_text(value: str | None, field_name: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise JournalValidationError(f"{field_name} must be a non-empty string")
    if len(value) > maximum:
        raise JournalValidationError(f"{field_name} exceeds {maximum} characters")
    return value


def _absolute_path_text(value: str | Path, field_name: str) -> str:
    if not isinstance(value, (str, os.PathLike)):
        raise JournalValidationError(f"{field_name} must be a filesystem path")
    path = Path(value)
    if not path.is_absolute():
        raise JournalValidationError(f"{field_name} must be absolute")
    return str(path)


def _optional_absolute_path_text(
    value: str | Path | None,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    return _absolute_path_text(value, field_name)


def _nonnegative_integer(value: int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise JournalValidationError(f"{field_name} must be a non-negative integer")
    return value


def _operation_reference(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise JournalValidationError(f"{field_name} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise JournalValidationError(f"{field_name} must be a UUID string") from error
    return str(parsed)


def _error_values(
    error_code: str | None,
    error_message: str | None,
) -> tuple[str | None, str | None]:
    if error_code is None and error_message is None:
        return None, None
    if error_code is None or not isinstance(error_code, str):
        raise JournalValidationError("error_code is required with an error message")
    if not _ERROR_CODE_PATTERN.fullmatch(error_code):
        raise JournalValidationError(
            "error_code must contain only uppercase letters, digits, '.', '-', or '_'"
        )
    if error_message is None:
        return error_code, None
    if not isinstance(error_message, str):
        raise JournalValidationError("error_message must be a string")
    sanitized = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in error_message
    )
    sanitized = " ".join(sanitized.split())
    return error_code, sanitized[:_MAX_ERROR_MESSAGE_LENGTH]


def _operation_from_row(row: sqlite3.Row) -> OperationRecord:
    return OperationRecord(
        operation_id=row["operation_id"],
        operation_type=OperationType(row["operation_type"]),
        physical_phase=PhysicalPhase(row["physical_phase"]),
        operation_status=OperationStatus(row["operation_status"]),
        source_path=row["source_path"],
        source_identity=row["source_identity"],
        source_hash=row["source_hash"],
        source_size=row["source_size"],
        source_mtime_ns=row["source_mtime_ns"],
        source_object_type=SourceObjectType(row["source_object_type"]),
        organized_root=row["organized_root"],
        intended_destination=row["intended_destination"],
        actual_destination=row["actual_destination"],
        duplicate_of_path=row["duplicate_of_path"],
        destination_identity=row["destination_identity"],
        destination_hash=row["destination_hash"],
        destination_size=row["destination_size"],
        temp_path=row["temp_path"],
        temp_identity=row["temp_identity"],
        staging_path=row["staging_path"],
        staging_identity=row["staging_identity"],
        move_mode=MoveMode(row["move_mode"]),
        started_at_utc=row["started_at_utc"],
        updated_at_utc=row["updated_at_utc"],
        published_at_utc=row["published_at_utc"],
        physically_committed_at_utc=row["physically_committed_at_utc"],
        completed_at_utc=row["completed_at_utc"],
        parent_operation_id=row["parent_operation_id"],
        inverse_of_operation_id=row["inverse_of_operation_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        application_version=row["application_version"],
    )


def _event_from_row(row: sqlite3.Row) -> OperationEvent:
    return OperationEvent(
        event_id=row["event_id"],
        operation_id=row["operation_id"],
        sequence_number=row["sequence_number"],
        event_kind=EventKind(row["event_kind"]),
        from_phase=(
            PhysicalPhase(row["from_phase"]) if row["from_phase"] else None
        ),
        to_phase=PhysicalPhase(row["to_phase"]) if row["to_phase"] else None,
        from_status=(
            OperationStatus(row["from_status"]) if row["from_status"] else None
        ),
        to_status=(
            OperationStatus(row["to_status"]) if row["to_status"] else None
        ),
        effect_type=(
            EffectType(row["effect_type"]) if row["effect_type"] else None
        ),
        from_effect_state=(
            EffectState(row["from_effect_state"])
            if row["from_effect_state"]
            else None
        ),
        to_effect_state=(
            EffectState(row["to_effect_state"])
            if row["to_effect_state"]
            else None
        ),
        error_code=row["error_code"],
        error_message=row["error_message"],
        occurred_at_utc=row["occurred_at_utc"],
    )


def _effect_from_row(row: sqlite3.Row) -> OperationEffect:
    return OperationEffect(
        operation_id=row["operation_id"],
        effect_type=EffectType(row["effect_type"]),
        state=EffectState(row["state"]),
        required=bool(row["required"]),
        updated_at_utc=row["updated_at_utc"],
        error_code=row["error_code"],
        error_message=row["error_message"],
    )


class OperationJournal:
    """Short-transaction SQLite evidence store; it performs no filesystem actions."""

    def __init__(self, database_path: str | Path | None = None):
        selected_path = (
            resolve_default_journal_path()
            if database_path is None
            else Path(database_path).expanduser()
        )
        if not selected_path.is_absolute():
            raise JournalPathError("The operation journal path must be absolute")
        if _is_network_path(selected_path):
            raise JournalPathError("The operation journal cannot use a network path")
        if selected_path.exists() and selected_path.is_dir():
            raise JournalPathError("The operation journal path is a directory")
        self.database_path = selected_path
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise JournalPathError(
                "Could not create the operation journal data directory"
            ) from error
        self._initialize_or_validate()

    def _open_sqlite(self, require_wal: bool) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=_BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            locking_mode = connection.execute(
                "PRAGMA locking_mode = NORMAL"
            ).fetchone()[0]
            if str(locking_mode).lower() != "normal":
                raise JournalDatabaseError(
                    "SQLite did not activate NORMAL locking mode"
                )
            if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise JournalDatabaseError("SQLite did not activate foreign keys")
            if connection.execute("PRAGMA synchronous").fetchone()[0] != 2:
                raise JournalDatabaseError("SQLite did not activate FULL synchronous mode")
            if require_wal:
                journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                if str(journal_mode).lower() != "wal":
                    raise JournalDatabaseError(
                        "The operation journal is not using WAL mode"
                    )
            return connection
        except Exception:
            connection.close()
            raise

    @staticmethod
    def _translate_database_error(error: sqlite3.DatabaseError):
        message = str(error).lower()
        if (
            "not a database" in message
            or "malformed" in message
            or "database disk image is malformed" in message
        ):
            raise JournalCorruptionError(
                "The operation journal is not a valid SQLite database"
            ) from error
        raise JournalDatabaseError("Operation journal database operation failed") from error

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = self._open_sqlite(require_wal=True)
            self._validate_current_identity(connection)
            yield connection
        except JournalError:
            raise
        except sqlite3.DatabaseError as error:
            self._translate_database_error(error)
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def _transaction(
        self,
        connection: sqlite3.Connection,
        *,
        validate_database: bool = True,
    ) -> Iterator[None]:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if validate_database:
                self._validate_current_identity(connection)
                self._validate_schema(connection)
            yield
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

    @contextmanager
    def _read_transaction(
        self,
        connection: sqlite3.Connection,
    ) -> Iterator[None]:
        connection.execute("BEGIN")
        try:
            self._validate_current_identity(connection)
            self._validate_schema(connection)
            yield
        finally:
            connection.rollback()

    @staticmethod
    def _identity(connection: sqlite3.Connection) -> tuple[int, int]:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        return application_id, user_version

    @classmethod
    def _validate_current_identity(cls, connection: sqlite3.Connection) -> None:
        application_id, version = cls._identity(connection)
        if application_id != APPLICATION_ID:
            raise JournalIncompatibleError(
                "The database is not the initialized FilePilot operation journal"
            )
        if version != SCHEMA_VERSION:
            raise JournalIncompatibleError(
                "The operation journal schema version changed after initialization"
            )

    @staticmethod
    def _has_user_schema(connection: sqlite3.Connection) -> bool:
        return (
            connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                LIMIT 1
                """
            ).fetchone()
            is not None
        )

    @staticmethod
    def _activate_wal(connection: sqlite3.Connection) -> None:
        activated = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if str(activated).lower() != "wal":
            raise JournalDatabaseError("SQLite did not activate WAL journal mode")

    def _initialize_or_validate(self) -> None:
        connection = None
        try:
            connection = self._open_sqlite(require_wal=False)
            application_id, version = self._identity(connection)
            has_schema = self._has_user_schema(connection)

            if application_id == 0:
                if version != 0 or has_schema:
                    raise JournalIncompatibleError(
                        "The database is not an empty FilePilot operation journal"
                    )
                self._activate_wal(connection)
                with self._transaction(connection, validate_database=False):
                    application_id, version = self._identity(connection)
                    if application_id != 0 or version != 0 or self._has_user_schema(
                        connection
                    ):
                        raise JournalConflictError(
                            "The operation journal was initialized concurrently"
                        )
                    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                    self._run_migrations(connection, version)
                    self._validate_current_identity(connection)
                    self._validate_schema(connection)
            elif application_id != APPLICATION_ID:
                raise JournalIncompatibleError(
                    "The database belongs to another application"
                )
            elif version > SCHEMA_VERSION:
                raise JournalIncompatibleError(
                    "The operation journal schema is newer than this FilePilot build"
                )
            elif version < SCHEMA_VERSION:
                self._activate_wal(connection)
                with self._transaction(connection, validate_database=False):
                    current_application_id, current_version = self._identity(connection)
                    if current_application_id != APPLICATION_ID:
                        raise JournalIncompatibleError(
                            "The operation journal identity changed during migration"
                        )
                    if current_version != version:
                        raise JournalConflictError(
                            "The operation journal version changed during migration"
                        )
                    self._run_migrations(connection, current_version)
                    self._validate_current_identity(connection)
                    self._validate_schema(connection)

            application_id, version = self._identity(connection)
            if application_id != APPLICATION_ID or version != SCHEMA_VERSION:
                raise JournalSchemaError(
                    "Operation journal identity or schema version was not committed"
                )
            self._validate_schema(connection)
            self._activate_wal(connection)
        except JournalError:
            raise
        except sqlite3.DatabaseError as error:
            self._translate_database_error(error)
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _run_migrations(connection: sqlite3.Connection, version: int) -> None:
        current_version = version
        while current_version < SCHEMA_VERSION:
            next_version = current_version + 1
            migration = _MIGRATIONS.get(next_version)
            if migration is None:
                raise JournalIncompatibleError(
                    f"No migration is available for schema version {next_version}"
                )
            migration(connection)
            connection.execute(f"PRAGMA user_version = {next_version}")
            current_version = next_version

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise JournalCorruptionError("The operation journal failed quick_check")

        expected_objects = {
            *(('table', table_name) for table_name in _EXPECTED_TABLE_SQL),
            *(('index', index_name) for index_name in _EXPECTED_INDEXES),
        }
        actual_objects = {
            (row["type"], row["name"])
            for row in connection.execute(
                """
                SELECT type, name FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                """
            )
        }
        if actual_objects != expected_objects:
            raise JournalSchemaError(
                "The operation journal contains unexpected or missing schema objects"
            )

        for table_name, expected_columns in _EXPECTED_COLUMNS.items():
            table = connection.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'table' AND name = ?
                """,
                (table_name,),
            ).fetchone()
            if table is None or table["sql"] is None:
                raise JournalSchemaError(
                    f"Operation journal table {table_name} is missing"
                )
            if _normalize_schema_sql(table["sql"]) != _normalize_schema_sql(
                _EXPECTED_TABLE_SQL[table_name]
            ):
                raise JournalSchemaError(
                    f"Operation journal table {table_name} has an incompatible definition"
                )

            rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            actual_columns = {row["name"] for row in rows}
            if actual_columns != expected_columns:
                raise JournalSchemaError(
                    f"Operation journal table {table_name} has an incompatible schema"
                )

        for index_name, (table_name, unique, expected_columns) in _EXPECTED_INDEXES.items():
            row = connection.execute(
                """
                SELECT tbl_name FROM sqlite_master
                WHERE type = 'index' AND name = ?
                """,
                (index_name,),
            ).fetchone()
            if row is None or row["tbl_name"] != table_name:
                raise JournalSchemaError(
                    f"Operation journal index {index_name} is missing or incompatible"
                )
            index_list_row = next(
                (
                    item
                    for item in connection.execute(
                        f'PRAGMA index_list("{table_name}")'
                    )
                    if item["name"] == index_name
                ),
                None,
            )
            actual_columns = tuple(
                item["name"]
                for item in connection.execute(f'PRAGMA index_info("{index_name}")')
            )
            if (
                index_list_row is None
                or bool(index_list_row["unique"]) is not unique
                or bool(index_list_row["partial"])
                or actual_columns != expected_columns
            ):
                raise JournalSchemaError(
                    f"Operation journal index {index_name} is incompatible"
                )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise JournalCorruptionError(
                "The operation journal contains invalid foreign-key references"
            )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        operation_id: str,
        event_kind: EventKind,
        occurred_at_utc: str,
        *,
        from_phase: PhysicalPhase | None = None,
        to_phase: PhysicalPhase | None = None,
        from_status: OperationStatus | None = None,
        to_status: OperationStatus | None = None,
        effect_type: EffectType | None = None,
        from_effect_state: EffectState | None = None,
        to_effect_state: EffectState | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        sequence_number = connection.execute(
            """
            SELECT COALESCE(MAX(sequence_number), 0) + 1
            FROM operation_events
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()[0]
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
                effect_type,
                from_effect_state,
                to_effect_state,
                error_code,
                error_message,
                occurred_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                sequence_number,
                event_kind.value,
                from_phase.value if from_phase else None,
                to_phase.value if to_phase else None,
                from_status.value if from_status else None,
                to_status.value if to_status else None,
                effect_type.value if effect_type else None,
                from_effect_state.value if from_effect_state else None,
                to_effect_state.value if to_effect_state else None,
                error_code,
                error_message,
                occurred_at_utc,
            ),
        )

    @staticmethod
    def _select_operation(
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> OperationRecord:
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise JournalNotFoundError(f"Operation not found: {operation_id}")
        return _operation_from_row(row)

    def database_settings(self) -> dict[str, int | str]:
        with self._connection() as connection:
            with self._read_transaction(connection):
                return {
                    "application_id": connection.execute(
                        "PRAGMA application_id"
                    ).fetchone()[0],
                    "user_version": connection.execute(
                        "PRAGMA user_version"
                    ).fetchone()[0],
                    "journal_mode": str(
                        connection.execute("PRAGMA journal_mode").fetchone()[0]
                    ).lower(),
                    "synchronous": connection.execute(
                        "PRAGMA synchronous"
                    ).fetchone()[0],
                    "foreign_keys": connection.execute(
                        "PRAGMA foreign_keys"
                    ).fetchone()[0],
                    "busy_timeout": connection.execute(
                        "PRAGMA busy_timeout"
                    ).fetchone()[0],
                    "locking_mode": str(
                        connection.execute("PRAGMA locking_mode").fetchone()[0]
                    ).lower(),
                    "quick_check": connection.execute(
                        "PRAGMA quick_check"
                    ).fetchone()[0],
                }

    def create_operation(
        self,
        *,
        source_path: str | Path,
        organized_root: str | Path,
        intended_destination: str | Path,
        operation_type: OperationType = OperationType.MOVE,
        source_identity: str | None = None,
        source_hash: str | None = None,
        source_size: int | None = None,
        source_mtime_ns: int | None = None,
        source_object_type: SourceObjectType = SourceObjectType.FILE,
        application_version: str | None = None,
        parent_operation_id: str | None = None,
        inverse_of_operation_id: str | None = None,
    ) -> OperationRecord:
        _required_enum(operation_type, OperationType, "operation_type")
        _required_enum(source_object_type, SourceObjectType, "source_object_type")
        values = {
            "operation_id": str(uuid.uuid4()),
            "operation_type": operation_type.value,
            "physical_phase": PhysicalPhase.PREPARED.value,
            "operation_status": OperationStatus.OPEN.value,
            "source_path": _absolute_path_text(source_path, "source_path"),
            "source_identity": _bounded_text(
                source_identity, "source_identity", 512
            ),
            "source_hash": _bounded_text(source_hash, "source_hash", 256),
            "source_size": _nonnegative_integer(source_size, "source_size"),
            "source_mtime_ns": _nonnegative_integer(
                source_mtime_ns, "source_mtime_ns"
            ),
            "source_object_type": source_object_type.value,
            "organized_root": _absolute_path_text(
                organized_root, "organized_root"
            ),
            "intended_destination": _absolute_path_text(
                intended_destination, "intended_destination"
            ),
            "move_mode": MoveMode.UNKNOWN.value,
            "timestamp": _utc_now(),
            "parent_operation_id": _operation_reference(
                parent_operation_id, "parent_operation_id"
            ),
            "inverse_of_operation_id": _operation_reference(
                inverse_of_operation_id, "inverse_of_operation_id"
            ),
            "application_version": _bounded_text(
                application_version, "application_version", 64
            ),
        }
        with self._connection() as connection:
            with self._transaction(connection):
                connection.execute(
                    """
                    INSERT INTO operations (
                        operation_id,
                        operation_type,
                        physical_phase,
                        operation_status,
                        source_path,
                        source_identity,
                        source_hash,
                        source_size,
                        source_mtime_ns,
                        source_object_type,
                        organized_root,
                        intended_destination,
                        move_mode,
                        started_at_utc,
                        updated_at_utc,
                        parent_operation_id,
                        inverse_of_operation_id,
                        application_version
                    ) VALUES (
                        :operation_id,
                        :operation_type,
                        :physical_phase,
                        :operation_status,
                        :source_path,
                        :source_identity,
                        :source_hash,
                        :source_size,
                        :source_mtime_ns,
                        :source_object_type,
                        :organized_root,
                        :intended_destination,
                        :move_mode,
                        :timestamp,
                        :timestamp,
                        :parent_operation_id,
                        :inverse_of_operation_id,
                        :application_version
                    )
                    """,
                    values,
                )
                self._append_event(
                    connection,
                    values["operation_id"],
                    EventKind.OPERATION_CREATED,
                    values["timestamp"],
                    to_phase=PhysicalPhase.PREPARED,
                    to_status=OperationStatus.OPEN,
                )
                return self._select_operation(connection, values["operation_id"])

    def get_operation(self, operation_id: str) -> OperationRecord:
        operation_id = _operation_reference(operation_id, "operation_id")
        with self._connection() as connection:
            with self._read_transaction(connection):
                return self._select_operation(connection, operation_id)

    def get_inverse_operation(
        self,
        original_operation_id: str,
    ) -> OperationRecord | None:
        original_operation_id = _operation_reference(
            original_operation_id, "original_operation_id"
        )
        with self._connection() as connection:
            with self._read_transaction(connection):
                row = connection.execute(
                    """
                    SELECT * FROM operations
                    WHERE inverse_of_operation_id = ?
                    ORDER BY started_at_utc, operation_id
                    LIMIT 1
                    """,
                    (original_operation_id,),
                ).fetchone()
                return _operation_from_row(row) if row is not None else None

    def create_inverse_operation(
        self,
        original_operation_id: str,
        *,
        source_path: str | Path,
        organized_root: str | Path,
        intended_destination: str | Path,
        source_identity: str,
        source_hash: str,
        source_size: int,
        source_mtime_ns: int,
        application_version: str | None = None,
    ) -> OperationRecord:
        """Atomically reserve the sole inverse operation for a completed move."""
        original_operation_id = _operation_reference(
            original_operation_id, "original_operation_id"
        )
        values = {
            "operation_id": str(uuid.uuid4()),
            "operation_type": OperationType.MOVE.value,
            "physical_phase": PhysicalPhase.PREPARED.value,
            "operation_status": OperationStatus.OPEN.value,
            "source_path": _absolute_path_text(source_path, "source_path"),
            "source_identity": _bounded_text(source_identity, "source_identity", 512),
            "source_hash": _bounded_text(source_hash, "source_hash", 256),
            "source_size": _nonnegative_integer(source_size, "source_size"),
            "source_mtime_ns": _nonnegative_integer(
                source_mtime_ns, "source_mtime_ns"
            ),
            "source_object_type": SourceObjectType.FILE.value,
            "organized_root": _absolute_path_text(organized_root, "organized_root"),
            "intended_destination": _absolute_path_text(
                intended_destination, "intended_destination"
            ),
            "move_mode": MoveMode.UNKNOWN.value,
            "timestamp": _utc_now(),
            "parent_operation_id": original_operation_id,
            "inverse_of_operation_id": original_operation_id,
            "application_version": _bounded_text(
                application_version, "application_version", 64
            ),
        }
        with self._connection() as connection:
            with self._transaction(connection):
                original = self._select_operation(connection, original_operation_id)
                if (
                    original.inverse_of_operation_id is not None
                    or original.operation_status is not OperationStatus.COMPLETE
                    or original.physical_phase is not PhysicalPhase.PHYSICAL_COMMITTED
                ):
                    raise JournalConflictError(
                        "Only a completed original move can have an inverse operation"
                    )
                existing = connection.execute(
                    "SELECT operation_id FROM operations WHERE inverse_of_operation_id = ?",
                    (original_operation_id,),
                ).fetchone()
                if existing is not None:
                    raise JournalConflictError(
                        "An inverse operation already exists for this move"
                    )
                connection.execute(
                    """
                    INSERT INTO operations (
                        operation_id, operation_type, physical_phase, operation_status,
                        source_path, source_identity, source_hash, source_size,
                        source_mtime_ns, source_object_type, organized_root,
                        intended_destination, move_mode, started_at_utc, updated_at_utc,
                        parent_operation_id, inverse_of_operation_id, application_version
                    ) VALUES (
                        :operation_id, :operation_type, :physical_phase, :operation_status,
                        :source_path, :source_identity, :source_hash, :source_size,
                        :source_mtime_ns, :source_object_type, :organized_root,
                        :intended_destination, :move_mode, :timestamp, :timestamp,
                        :parent_operation_id, :inverse_of_operation_id, :application_version
                    )
                    """,
                    values,
                )
                self._append_event(
                    connection,
                    values["operation_id"],
                    EventKind.OPERATION_CREATED,
                    values["timestamp"],
                    to_phase=PhysicalPhase.PREPARED,
                    to_status=OperationStatus.OPEN,
                )
                return self._select_operation(connection, values["operation_id"])

    def list_incomplete_operations(self) -> list[OperationRecord]:
        with self._connection() as connection:
            with self._read_transaction(connection):
                rows = connection.execute(
                    """
                    SELECT * FROM operations
                    WHERE operation_status IN (?, ?)
                    ORDER BY started_at_utc, operation_id
                    """,
                    (
                        OperationStatus.OPEN.value,
                        OperationStatus.NEEDS_REVIEW.value,
                    ),
                ).fetchall()
                return [_operation_from_row(row) for row in rows]

    def get_events(self, operation_id: str) -> list[OperationEvent]:
        operation_id = _operation_reference(operation_id, "operation_id")
        with self._connection() as connection:
            with self._read_transaction(connection):
                if connection.execute(
                    "SELECT 1 FROM operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone() is None:
                    raise JournalNotFoundError(f"Operation not found: {operation_id}")
                rows = connection.execute(
                    """
                    SELECT * FROM operation_events
                    WHERE operation_id = ?
                    ORDER BY sequence_number
                    """,
                    (operation_id,),
                ).fetchall()
                return [_event_from_row(row) for row in rows]

    def transition_phase(
        self,
        operation_id: str,
        expected_phase: PhysicalPhase,
        new_phase: PhysicalPhase,
        *,
        evidence: TransitionEvidence | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> OperationRecord:
        operation_id = _operation_reference(operation_id, "operation_id")
        _required_enum(expected_phase, PhysicalPhase, "expected_phase")
        _required_enum(new_phase, PhysicalPhase, "new_phase")
        if new_phase not in _PHASE_TRANSITIONS[expected_phase]:
            raise JournalValidationError(
                f"Illegal physical transition: {expected_phase.value} -> {new_phase.value}"
            )
        if evidence is not None and not isinstance(evidence, TransitionEvidence):
            raise JournalValidationError("evidence must be TransitionEvidence")
        if error_code is not None or error_message is not None:
            error_code, error_message = _error_values(error_code, error_message)
        if new_phase not in {PhysicalPhase.ABORTED, PhysicalPhase.NEEDS_REVIEW} and (
            error_code is not None or error_message is not None
        ):
            raise JournalValidationError(
                "Phase errors may only be stored for aborted or review transitions"
            )

        new_status = OperationStatus.OPEN
        if new_phase is PhysicalPhase.ABORTED:
            new_status = OperationStatus.ABORTED
        elif new_phase is PhysicalPhase.NEEDS_REVIEW:
            new_status = OperationStatus.NEEDS_REVIEW

        timestamp = _utc_now()
        assignments = [
            "physical_phase = ?",
            "operation_status = ?",
            "updated_at_utc = ?",
        ]
        parameters: list[object] = [new_phase.value, new_status.value, timestamp]

        if new_phase is PhysicalPhase.RENAME_INTENT:
            assignments.append("move_mode = ?")
            parameters.append(MoveMode.SAME_VOLUME.value)
        elif new_phase is PhysicalPhase.TEMP_CREATE_INTENT:
            assignments.append("move_mode = ?")
            parameters.append(MoveMode.CROSS_VOLUME.value)
        if new_phase is PhysicalPhase.DESTINATION_PUBLISHED:
            assignments.append("published_at_utc = ?")
            parameters.append(timestamp)
        if new_phase is PhysicalPhase.PHYSICAL_COMMITTED:
            assignments.append("physically_committed_at_utc = ?")
            parameters.append(timestamp)
        if new_phase in {PhysicalPhase.ABORTED, PhysicalPhase.NEEDS_REVIEW}:
            assignments.extend(["error_code = ?", "error_message = ?"])
            parameters.extend([error_code, error_message])

        if evidence is not None:
            evidence_values = {
                "actual_destination": _optional_absolute_path_text(
                    evidence.actual_destination, "actual_destination"
                ),
                "destination_identity": _bounded_text(
                    evidence.destination_identity, "destination_identity", 512
                ),
                "destination_hash": _bounded_text(
                    evidence.destination_hash, "destination_hash", 256
                ),
                "destination_size": _nonnegative_integer(
                    evidence.destination_size, "destination_size"
                ),
                "temp_path": _optional_absolute_path_text(
                    evidence.temp_path, "temp_path"
                ),
                "temp_identity": _bounded_text(
                    evidence.temp_identity, "temp_identity", 512
                ),
                "staging_path": _optional_absolute_path_text(
                    evidence.staging_path, "staging_path"
                ),
                "staging_identity": _bounded_text(
                    evidence.staging_identity, "staging_identity", 512
                ),
            }
            for column_name, value in evidence_values.items():
                if value is not None:
                    assignments.append(f"{column_name} = ?")
                    parameters.append(value)

        parameters.extend(
            [
                operation_id,
                expected_phase.value,
                OperationStatus.OPEN.value,
            ]
        )
        with self._connection() as connection:
            with self._transaction(connection):
                cursor = connection.execute(
                    f"""
                    UPDATE operations
                    SET {', '.join(assignments)}
                    WHERE operation_id = ?
                      AND physical_phase = ?
                      AND operation_status = ?
                    """,
                    parameters,
                )
                if cursor.rowcount != 1:
                    current = connection.execute(
                        """
                        SELECT physical_phase, operation_status
                        FROM operations WHERE operation_id = ?
                        """,
                        (operation_id,),
                    ).fetchone()
                    if current is None:
                        raise JournalNotFoundError(
                            f"Operation not found: {operation_id}"
                        )
                    raise JournalConflictError(
                        "Operation phase or status changed before transition"
                    )
                self._append_event(
                    connection,
                    operation_id,
                    EventKind.PHASE_TRANSITION,
                    timestamp,
                    from_phase=expected_phase,
                    to_phase=new_phase,
                    from_status=OperationStatus.OPEN,
                    to_status=new_status,
                    error_code=error_code,
                    error_message=error_message,
                )
                return self._select_operation(connection, operation_id)

    def mark_duplicate(
        self,
        operation_id: str,
        *,
        expected_phase: PhysicalPhase,
        duplicate_of_path: str | Path,
    ) -> OperationRecord:
        operation_id = _operation_reference(operation_id, "operation_id")
        _required_enum(expected_phase, PhysicalPhase, "expected_phase")
        if expected_phase is not PhysicalPhase.PREPARED:
            raise JournalValidationError(
                "A duplicate outcome may only be recorded from PREPARED"
            )
        duplicate_path = _absolute_path_text(
            duplicate_of_path, "duplicate_of_path"
        )
        timestamp = _utc_now()
        with self._connection() as connection:
            with self._transaction(connection):
                cursor = connection.execute(
                    """
                    UPDATE operations
                    SET operation_status = ?, duplicate_of_path = ?, updated_at_utc = ?
                    WHERE operation_id = ?
                      AND physical_phase = ?
                      AND operation_status = ?
                    """,
                    (
                        OperationStatus.DUPLICATE.value,
                        duplicate_path,
                        timestamp,
                        operation_id,
                        expected_phase.value,
                        OperationStatus.OPEN.value,
                    ),
                )
                if cursor.rowcount != 1:
                    if connection.execute(
                        "SELECT 1 FROM operations WHERE operation_id = ?",
                        (operation_id,),
                    ).fetchone() is None:
                        raise JournalNotFoundError(
                            f"Operation not found: {operation_id}"
                        )
                    raise JournalConflictError(
                        "Operation phase or status changed before duplicate outcome"
                    )
                self._append_event(
                    connection,
                    operation_id,
                    EventKind.OUTCOME_RECORDED,
                    timestamp,
                    from_phase=expected_phase,
                    to_phase=expected_phase,
                    from_status=OperationStatus.OPEN,
                    to_status=OperationStatus.DUPLICATE,
                )
                return self._select_operation(connection, operation_id)

    def complete_operation(self, operation_id: str) -> OperationRecord:
        """Complete a physical operation only after every required effect applied."""
        operation_id = _operation_reference(operation_id, "operation_id")
        timestamp = _utc_now()
        with self._connection() as connection:
            with self._transaction(connection):
                operation = self._select_operation(connection, operation_id)
                if (
                    operation.operation_status is not OperationStatus.OPEN
                    or operation.physical_phase is not PhysicalPhase.PHYSICAL_COMMITTED
                ):
                    raise JournalConflictError(
                        "Only an open physically committed operation can complete"
                    )
                missing_required = connection.execute(
                    """
                    SELECT 1 FROM operation_effects
                    WHERE operation_id = ? AND required = 1 AND state != ?
                    LIMIT 1
                    """,
                    (operation_id, EffectState.APPLIED.value),
                ).fetchone()
                if missing_required is not None:
                    raise JournalConflictError(
                        "Required operation effects have not all been applied"
                    )
                cursor = connection.execute(
                    """
                    UPDATE operations
                    SET operation_status = ?, updated_at_utc = ?, completed_at_utc = ?
                    WHERE operation_id = ? AND physical_phase = ? AND operation_status = ?
                    """,
                    (
                        OperationStatus.COMPLETE.value,
                        timestamp,
                        timestamp,
                        operation_id,
                        PhysicalPhase.PHYSICAL_COMMITTED.value,
                        OperationStatus.OPEN.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise JournalConflictError(
                        "Operation phase or status changed before completion"
                    )
                self._append_event(
                    connection,
                    operation_id,
                    EventKind.OUTCOME_RECORDED,
                    timestamp,
                    from_phase=PhysicalPhase.PHYSICAL_COMMITTED,
                    to_phase=PhysicalPhase.PHYSICAL_COMMITTED,
                    from_status=OperationStatus.OPEN,
                    to_status=OperationStatus.COMPLETE,
                )
                return self._select_operation(connection, operation_id)

    def mark_needs_review(
        self,
        operation_id: str,
        *,
        expected_phase: PhysicalPhase,
        error_code: str,
        error_message: str,
    ) -> OperationRecord:
        """Stop automatic recovery without discarding the last physical phase."""
        operation_id = _operation_reference(operation_id, "operation_id")
        _required_enum(expected_phase, PhysicalPhase, "expected_phase")
        error_code, error_message = _error_values(error_code, error_message)
        timestamp = _utc_now()
        with self._connection() as connection:
            with self._transaction(connection):
                cursor = connection.execute(
                    """
                    UPDATE operations
                    SET operation_status = ?, updated_at_utc = ?,
                        error_code = ?, error_message = ?
                    WHERE operation_id = ? AND physical_phase = ?
                      AND operation_status = ?
                    """,
                    (
                        OperationStatus.NEEDS_REVIEW.value,
                        timestamp,
                        error_code,
                        error_message,
                        operation_id,
                        expected_phase.value,
                        OperationStatus.OPEN.value,
                    ),
                )
                if cursor.rowcount != 1:
                    if connection.execute(
                        "SELECT 1 FROM operations WHERE operation_id = ?",
                        (operation_id,),
                    ).fetchone() is None:
                        raise JournalNotFoundError(
                            f"Operation not found: {operation_id}"
                        )
                    raise JournalConflictError(
                        "Operation phase or status changed before review outcome"
                    )
                self._append_event(
                    connection,
                    operation_id,
                    EventKind.OUTCOME_RECORDED,
                    timestamp,
                    from_phase=expected_phase,
                    to_phase=expected_phase,
                    from_status=OperationStatus.OPEN,
                    to_status=OperationStatus.NEEDS_REVIEW,
                    error_code=error_code,
                    error_message=error_message,
                )
                return self._select_operation(connection, operation_id)

    def initialize_effect(
        self,
        operation_id: str,
        effect_type: EffectType,
        *,
        required: bool = True,
        initial_state: EffectState = EffectState.NOT_STARTED,
    ) -> OperationEffect:
        operation_id = _operation_reference(operation_id, "operation_id")
        _required_enum(effect_type, EffectType, "effect_type")
        _required_enum(initial_state, EffectState, "initial_state")
        if initial_state not in {EffectState.NOT_STARTED, EffectState.PENDING}:
            raise JournalValidationError(
                "An effect must initialize as NOT_STARTED or PENDING"
            )
        if not isinstance(required, bool):
            raise JournalValidationError("required must be a bool")
        timestamp = _utc_now()
        with self._connection() as connection:
            with self._transaction(connection):
                if connection.execute(
                    "SELECT 1 FROM operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone() is None:
                    raise JournalNotFoundError(f"Operation not found: {operation_id}")
                try:
                    connection.execute(
                        """
                        INSERT INTO operation_effects (
                            operation_id,
                            effect_type,
                            state,
                            required,
                            updated_at_utc
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            operation_id,
                            effect_type.value,
                            initial_state.value,
                            int(required),
                            timestamp,
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise JournalConflictError(
                        "The effect is already initialized for this operation"
                    ) from error
                connection.execute(
                    "UPDATE operations SET updated_at_utc = ? WHERE operation_id = ?",
                    (timestamp, operation_id),
                )
                self._append_event(
                    connection,
                    operation_id,
                    EventKind.EFFECT_INITIALIZED,
                    timestamp,
                    effect_type=effect_type,
                    to_effect_state=initial_state,
                )
                row = connection.execute(
                    """
                    SELECT * FROM operation_effects
                    WHERE operation_id = ? AND effect_type = ?
                    """,
                    (operation_id, effect_type.value),
                ).fetchone()
                return _effect_from_row(row)

    def transition_effect(
        self,
        operation_id: str,
        effect_type: EffectType,
        expected_state: EffectState,
        new_state: EffectState,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> OperationEffect:
        operation_id = _operation_reference(operation_id, "operation_id")
        _required_enum(effect_type, EffectType, "effect_type")
        _required_enum(expected_state, EffectState, "expected_state")
        _required_enum(new_state, EffectState, "new_state")
        if new_state not in _EFFECT_TRANSITIONS[expected_state]:
            raise JournalValidationError(
                f"Illegal effect transition: {expected_state.value} -> {new_state.value}"
            )
        if error_code is not None or error_message is not None:
            error_code, error_message = _error_values(error_code, error_message)
        if new_state not in {EffectState.FAILED, EffectState.UNKNOWN} and (
            error_code is not None or error_message is not None
        ):
            raise JournalValidationError(
                "Effect errors may only be stored for FAILED or UNKNOWN"
            )
        timestamp = _utc_now()
        with self._connection() as connection:
            with self._transaction(connection):
                cursor = connection.execute(
                    """
                    UPDATE operation_effects
                    SET state = ?, updated_at_utc = ?, error_code = ?, error_message = ?
                    WHERE operation_id = ? AND effect_type = ? AND state = ?
                    """,
                    (
                        new_state.value,
                        timestamp,
                        error_code,
                        error_message,
                        operation_id,
                        effect_type.value,
                        expected_state.value,
                    ),
                )
                if cursor.rowcount != 1:
                    current = connection.execute(
                        """
                        SELECT state FROM operation_effects
                        WHERE operation_id = ? AND effect_type = ?
                        """,
                        (operation_id, effect_type.value),
                    ).fetchone()
                    if current is None:
                        if connection.execute(
                            "SELECT 1 FROM operations WHERE operation_id = ?",
                            (operation_id,),
                        ).fetchone() is None:
                            raise JournalNotFoundError(
                                f"Operation not found: {operation_id}"
                            )
                        raise JournalNotFoundError(
                            f"Effect not found: {effect_type.value}"
                        )
                    raise JournalConflictError(
                        "Effect state changed before transition"
                    )
                connection.execute(
                    "UPDATE operations SET updated_at_utc = ? WHERE operation_id = ?",
                    (timestamp, operation_id),
                )
                self._append_event(
                    connection,
                    operation_id,
                    EventKind.EFFECT_TRANSITION,
                    timestamp,
                    effect_type=effect_type,
                    from_effect_state=expected_state,
                    to_effect_state=new_state,
                    error_code=error_code,
                    error_message=error_message,
                )
                row = connection.execute(
                    """
                    SELECT * FROM operation_effects
                    WHERE operation_id = ? AND effect_type = ?
                    """,
                    (operation_id, effect_type.value),
                ).fetchone()
                return _effect_from_row(row)

    def get_effects(self, operation_id: str) -> list[OperationEffect]:
        operation_id = _operation_reference(operation_id, "operation_id")
        with self._connection() as connection:
            with self._read_transaction(connection):
                if connection.execute(
                    "SELECT 1 FROM operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone() is None:
                    raise JournalNotFoundError(f"Operation not found: {operation_id}")
                rows = connection.execute(
                    """
                    SELECT * FROM operation_effects
                    WHERE operation_id = ?
                    ORDER BY effect_type
                    """,
                    (operation_id,),
                ).fetchall()
                return [_effect_from_row(row) for row in rows]
