from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable

from app.config_loader import resolve_runtime_path
from app.mover import UnsafeDestinationError, validate_category
from app.path_topology import UnsafePathTopologyError, validate_path_topology


FALLBACK_CATEGORY = "others"
_EXTENSION_PATTERN = re.compile(r"^\.[^.\s/\\*?:\"<>|]+$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ConfigurationDataState(str, Enum):
    LOADING = "loading"
    AVAILABLE = "available"
    EMPTY = "empty"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ConfigurationSaveStatus(str, Enum):
    SAVED = "saved"
    INVALID = "invalid"
    STALE = "stale"
    NOT_ALLOWED = "not_allowed"
    WRITE_FAILED = "write_failed"
    RELOAD_FAILED = "reload_failed"
    ROLLBACK_FAILED = "rollback_failed"


@dataclass(frozen=True)
class ConfigurationValidationIssue:
    field: str
    code: str
    message: str
    related_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductWatchFolder:
    path: Path | str
    label: str = ""
    active: bool = True
    exists: bool | None = None


@dataclass(frozen=True)
class ProductFolderSettings:
    watch_folders: tuple[ProductWatchFolder, ...]
    organized_folder: Path
    organized_exists: bool
    archive_by_date: bool
    topology_valid: bool
    changes_allowed: bool


@dataclass(frozen=True)
class ProductRule:
    category: str
    extensions: tuple[str, ...]


@dataclass(frozen=True)
class ProductConfigurationCandidate:
    watch_folders: tuple[ProductWatchFolder, ...]
    organized_folder: Path | str
    archive_by_date: bool
    rules: tuple[ProductRule, ...]


@dataclass(frozen=True)
class ConfigurationValidationResult:
    valid: bool
    candidate: ProductConfigurationCandidate
    issues: tuple[ConfigurationValidationIssue, ...] = ()


@dataclass(frozen=True)
class ProductConfigurationSnapshot:
    state: ConfigurationDataState
    revision: str | None = None
    folders: ProductFolderSettings | None = None
    rules: tuple[ProductRule, ...] = ()
    fallback_category: str = FALLBACK_CATEGORY
    issues: tuple[ConfigurationValidationIssue, ...] = ()
    error: str | None = None

    @classmethod
    def loading(cls) -> "ProductConfigurationSnapshot":
        return cls(ConfigurationDataState.LOADING)

    @classmethod
    def unavailable(cls, message: str) -> "ProductConfigurationSnapshot":
        return cls(ConfigurationDataState.UNAVAILABLE, error=message)

    @property
    def candidate(self) -> ProductConfigurationCandidate | None:
        if self.folders is None:
            return None
        return ProductConfigurationCandidate(
            self.folders.watch_folders,
            self.folders.organized_folder,
            self.folders.archive_by_date,
            self.rules,
        )


@dataclass(frozen=True)
class ConfigurationSaveResult:
    status: ConfigurationSaveStatus
    message: str
    snapshot: ProductConfigurationSnapshot | None = None
    issues: tuple[ConfigurationValidationIssue, ...] = ()


@dataclass(frozen=True)
class CandidateClassificationPreview:
    valid: bool
    filename: str
    normalized_extension: str
    category: str | None
    fallback: bool
    issues: tuple[ConfigurationValidationIssue, ...] = ()
    message: str = ""


class ProductConfigurationError(RuntimeError):
    pass


class StaleConfigurationError(ProductConfigurationError):
    pass


class ProductConfigurationStore:
    """Product-facing configuration projection, validation, and persistence."""

    def __init__(
        self,
        config_path: str | Path,
        *,
        path_resolver: Callable[[str], Path] = resolve_runtime_path,
    ) -> None:
        self.config_path = Path(config_path)
        self._path_resolver = path_resolver

    def read_snapshot(self, *, changes_allowed: bool) -> ProductConfigurationSnapshot:
        try:
            document, revision, _ = self.read_document()
            candidate = self.candidate_from_document(document)
            validation = self.validate(candidate)
        except FileNotFoundError:
            return ProductConfigurationSnapshot(
                ConfigurationDataState.EMPTY,
                error="FilePilot configuration is not available.",
            )
        except (json.JSONDecodeError, ProductConfigurationError) as error:
            return ProductConfigurationSnapshot(
                ConfigurationDataState.INVALID,
                error=f"FilePilot configuration is invalid: {error}",
            )
        except OSError as error:
            return ProductConfigurationSnapshot(
                ConfigurationDataState.ERROR,
                error=f"FilePilot configuration could not be read safely: {error}",
            )

        folders = ProductFolderSettings(
            watch_folders=tuple(
                replace(folder, exists=folder.path.is_dir())
                for folder in validation.candidate.watch_folders
            ),
            organized_folder=validation.candidate.organized_folder,
            organized_exists=validation.candidate.organized_folder.is_dir(),
            archive_by_date=validation.candidate.archive_by_date,
            topology_valid=not any(
                issue.field.startswith("folders.") for issue in validation.issues
            ),
            changes_allowed=changes_allowed,
        )
        return ProductConfigurationSnapshot(
            ConfigurationDataState.AVAILABLE,
            revision,
            folders,
            validation.candidate.rules,
            issues=validation.issues,
        )

    def read_document(self) -> tuple[dict, str, bytes]:
        data = self.config_path.read_bytes()
        document = json.loads(data.decode("utf-8"))
        if not isinstance(document, dict):
            raise ProductConfigurationError("Configuration root must be an object")
        return document, configuration_revision(document), data

    def candidate_from_document(self, document: dict) -> ProductConfigurationCandidate:
        organized = document.get("organized_base_folder")
        if not isinstance(organized, str) or not organized.strip():
            raise ProductConfigurationError("organized_base_folder is missing")
        organized_path = self._resolve_path(organized)

        raw_folders = document.get("watch_folders")
        if raw_folders is None:
            source = document.get("source_folder")
            raw_folders = (
                [{"path": source, "label": "Incoming", "active": True}]
                if isinstance(source, str) and source.strip()
                else []
            )
        if not isinstance(raw_folders, list):
            raise ProductConfigurationError("watch_folders must be a list")
        folders = []
        for index, item in enumerate(raw_folders):
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ProductConfigurationError(
                    f"Watch folder {index + 1} must define a path"
                )
            label = item.get("label", "")
            active = item.get("active", True)
            if not isinstance(label, str):
                raise ProductConfigurationError(
                    f"Watch folder {index + 1} label must be text"
                )
            if not isinstance(active, bool):
                raise ProductConfigurationError(
                    f"Watch folder {index + 1} active state must be true or false"
                )
            folders.append(
                ProductWatchFolder(
                    (
                        self._resolve_path(item["path"])
                        if item["path"].strip()
                        else item["path"]
                    ),
                    label.strip(),
                    active,
                )
            )

        raw_rules = document.get("rules", {})
        if not isinstance(raw_rules, dict):
            raise ProductConfigurationError("rules must be an object")
        rules = []
        for category, extensions in raw_rules.items():
            if not isinstance(extensions, list):
                raise ProductConfigurationError(
                    f"Extensions for {category!r} must be a list"
                )
            rules.append(ProductRule(str(category), tuple(extensions)))

        archive_by_date = document.get("archive_by_date", False)
        return ProductConfigurationCandidate(
            tuple(folders),
            organized_path,
            archive_by_date,
            tuple(rules),
        )

    def validate(
        self,
        candidate: ProductConfigurationCandidate,
    ) -> ConfigurationValidationResult:
        folder_candidate, folder_issues = self._validate_folders(candidate)
        rules, rule_issues = validate_product_rules(candidate.rules)
        normalized = ProductConfigurationCandidate(
            folder_candidate.watch_folders,
            folder_candidate.organized_folder,
            folder_candidate.archive_by_date,
            rules,
        )
        issues = tuple((*folder_issues, *rule_issues))
        return ConfigurationValidationResult(not issues, normalized, issues)

    def preview_classification(
        self,
        rules: tuple[ProductRule, ...],
        filename: str,
    ) -> CandidateClassificationPreview:
        normalized_rules, issues = validate_product_rules(rules)
        clean_name = filename.strip()
        extension = Path(clean_name).suffix.lower().strip() if clean_name else ""
        if issues:
            return CandidateClassificationPreview(
                False,
                clean_name,
                extension,
                None,
                False,
                issues,
                "Fix rule conflicts before testing this filename.",
            )
        lookup = {
            extension: rule.category
            for rule in normalized_rules
            for extension in rule.extensions
        }
        category = lookup.get(extension, FALLBACK_CATEGORY)
        fallback = category == FALLBACK_CATEGORY and extension not in lookup
        message = (
            f"{extension or 'No extension'} uses the fallback category."
            if fallback
            else f"{extension} is assigned to {category}."
        )
        return CandidateClassificationPreview(
            True,
            clean_name,
            extension,
            category,
            fallback,
            message=message,
        )

    def document_for_candidate(
        self,
        current: dict,
        candidate: ProductConfigurationCandidate,
    ) -> dict:
        validation = self.validate(candidate)
        if not validation.valid:
            raise ProductConfigurationError("Candidate configuration is invalid")
        normalized = validation.candidate
        document = dict(current)
        document["watch_folders"] = [
            {
                "path": str(folder.path),
                "label": folder.label or folder.path.name or "Incoming",
                "active": folder.active,
            }
            for folder in normalized.watch_folders
        ]
        legacy_source = next(
            (folder.path for folder in normalized.watch_folders if folder.active),
            normalized.watch_folders[0].path,
        )
        document["source_folder"] = str(legacy_source)
        document["organized_base_folder"] = str(normalized.organized_folder)
        document["archive_by_date"] = normalized.archive_by_date
        document["rules"] = {
            rule.category: list(rule.extensions) for rule in normalized.rules
        }
        destinations = {
            rule.category: str(normalized.organized_folder / rule.category)
            for rule in normalized.rules
        }
        destinations.setdefault(
            FALLBACK_CATEGORY,
            str(normalized.organized_folder / FALLBACK_CATEGORY),
        )
        document["destination_folders"] = destinations
        return document

    def write_document_atomic(
        self,
        document: dict,
        *,
        expected_revision: str | None = None,
    ) -> None:
        serialized = (
            json.dumps(
                document,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.write_bytes_atomic(
            serialized,
            expected_revision=expected_revision,
        )

    def write_bytes_atomic(
        self,
        data: bytes,
        *,
        expected_revision: str | None = None,
    ) -> None:
        parent = self.config_path.parent
        if not parent.is_dir():
            raise ProductConfigurationError(
                "Configuration directory is not available"
            )
        temporary_path: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{self.config_path.name}.",
                suffix=".tmp",
                dir=parent,
            )
            temporary_path = Path(name)
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            if expected_revision is not None:
                try:
                    current = json.loads(self.config_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise StaleConfigurationError(
                        "Configuration changed while the save was in progress"
                    ) from error
                if (
                    not isinstance(current, dict)
                    or configuration_revision(current) != expected_revision
                ):
                    raise StaleConfigurationError(
                        "Configuration changed while the save was in progress"
                    )
            os.replace(temporary_path, self.config_path)
            temporary_path = None
            _sync_directory(parent)
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    def _resolve_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        return (
            path.resolve(strict=False)
            if path.is_absolute()
            else Path(self._path_resolver(value)).resolve(strict=False)
        )

    def _validate_folders(
        self,
        candidate: ProductConfigurationCandidate,
    ) -> tuple[ProductConfigurationCandidate, tuple[ConfigurationValidationIssue, ...]]:
        issues = []
        folders = []
        if not candidate.watch_folders:
            issues.append(
                ConfigurationValidationIssue(
                    "folders.watch",
                    "WATCH_FOLDER_REQUIRED",
                    "Add at least one incoming folder.",
                )
            )
        seen_paths: dict[str, int] = {}
        for index, folder in enumerate(candidate.watch_folders):
            field = f"folders.watch.{index}"
            text = str(folder.path).strip()
            path = self._resolve_path(text) if text else Path()
            label = folder.label if isinstance(folder.label, str) else ""
            active = folder.active if isinstance(folder.active, bool) else False
            if not isinstance(folder.label, str):
                issues.append(
                    ConfigurationValidationIssue(
                        f"{field}.label",
                        "INVALID_WATCH_FOLDER_LABEL",
                        "Incoming folder labels must be text.",
                    )
                )
            if not isinstance(folder.active, bool):
                issues.append(
                    ConfigurationValidationIssue(
                        f"{field}.active",
                        "INVALID_WATCH_FOLDER_STATE",
                        "Incoming folder state must be enabled or disabled.",
                    )
                )
            if not text:
                issues.append(
                    ConfigurationValidationIssue(
                        field,
                        "EMPTY_WATCH_FOLDER",
                        "Incoming folder paths cannot be empty.",
                    )
                )
                continue
            key = os.path.normcase(str(path))
            if key in seen_paths:
                issues.append(
                    ConfigurationValidationIssue(
                        field,
                        "DUPLICATE_WATCH_FOLDER",
                        "The same incoming folder is listed more than once.",
                        (f"folders.watch.{seen_paths[key]}", field),
                    )
                )
            else:
                seen_paths[key] = index
            if active and not path.is_dir():
                issues.append(
                    ConfigurationValidationIssue(
                        field,
                        "WATCH_FOLDER_NOT_FOUND",
                        f"Incoming folder does not exist: {path}",
                    )
                )
            folders.append(
                replace(
                    folder,
                    path=path,
                    label=label.strip(),
                    active=active,
                    exists=path.is_dir(),
                )
            )

        organized_text = str(candidate.organized_folder).strip()
        organized = self._resolve_path(organized_text) if organized_text else Path()
        if not organized_text:
            issues.append(
                ConfigurationValidationIssue(
                    "folders.organized",
                    "EMPTY_ORGANIZED_FOLDER",
                    "Choose an organized folder.",
                )
            )
        elif not organized.is_dir():
            issues.append(
                ConfigurationValidationIssue(
                    "folders.organized",
                    "ORGANIZED_FOLDER_NOT_FOUND",
                    f"Organized folder does not exist: {organized}",
                )
            )
        if not isinstance(candidate.archive_by_date, bool):
            issues.append(
                ConfigurationValidationIssue(
                    "folders.archive_by_date",
                    "INVALID_ARCHIVE_SETTING",
                    "Archive by date must be enabled or disabled.",
                )
            )
        active_folders = [folder for folder in folders if folder.active]
        if not active_folders:
            issues.append(
                ConfigurationValidationIssue(
                    "folders.watch",
                    "ACTIVE_WATCH_FOLDER_REQUIRED",
                    "Enable at least one incoming folder.",
                )
            )
        for index, folder in enumerate(folders):
            for earlier in folders[:index]:
                if folder.path != earlier.path and (
                    folder.path.is_relative_to(earlier.path)
                    or earlier.path.is_relative_to(folder.path)
                ):
                    issues.append(
                        ConfigurationValidationIssue(
                            "folders.watch",
                            "OVERLAPPING_WATCH_FOLDERS",
                            "Configured incoming folders cannot contain one another.",
                        )
                    )
                    break
        try:
            validate_path_topology(
                (folder.path for folder in folders),
                organized,
            )
        except (UnsafePathTopologyError, OSError, RuntimeError, ValueError) as error:
            issues.append(
                ConfigurationValidationIssue(
                    "folders.topology",
                    "UNSAFE_PATH_TOPOLOGY",
                    str(error),
                    tuple(
                        ["folders.organized"]
                        + [f"folders.watch.{i}" for i in range(len(folders))]
                    ),
                )
            )
        normalized = replace(
            candidate,
            watch_folders=tuple(folders),
            organized_folder=organized,
            archive_by_date=(
                candidate.archive_by_date
                if isinstance(candidate.archive_by_date, bool)
                else False
            ),
        )
        return normalized, tuple(issues)


def normalize_extension(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    extension = value.strip().lower()
    if extension.startswith("*."):
        extension = extension[1:]
    elif extension.startswith("*"):
        return None
    if extension and not extension.startswith("."):
        extension = "." + extension
    if not _EXTENSION_PATTERN.fullmatch(extension):
        return None
    return extension


def validate_product_rules(
    rules: tuple[ProductRule, ...],
) -> tuple[tuple[ProductRule, ...], tuple[ConfigurationValidationIssue, ...]]:
    issues = []
    normalized_rules = []
    categories: dict[str, str] = {}
    extension_owners: dict[str, tuple[str, str]] = {}
    for rule_index, rule in enumerate(rules):
        category = rule.category.strip() if isinstance(rule.category, str) else ""
        category_field = f"rules.{rule_index}.category"
        if not category:
            issues.append(
                ConfigurationValidationIssue(
                    category_field,
                    "EMPTY_CATEGORY",
                    "Category names cannot be empty.",
                )
            )
        else:
            try:
                validate_category(category)
                if (
                    any(character in category for character in '/\\:*?"<>|\0')
                    or category.rstrip(". ") != category
                    or category.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
                ):
                    raise UnsafeDestinationError(
                        f"Unsafe category name: {category!r}"
                    )
            except UnsafeDestinationError as error:
                issues.append(
                    ConfigurationValidationIssue(
                        category_field,
                        "UNSAFE_CATEGORY",
                        str(error),
                    )
                )
            category_key = category.casefold()
            if category_key in categories:
                other = categories[category_key]
                issues.append(
                    ConfigurationValidationIssue(
                        category_field,
                        "DUPLICATE_CATEGORY",
                        f"Categories {other!r} and {category!r} conflict.",
                    )
                )
            else:
                categories[category_key] = category

        normalized_extensions = []
        if not rule.extensions:
            issues.append(
                ConfigurationValidationIssue(
                    f"rules.{rule_index}.extensions",
                    "EMPTY_EXTENSION_LIST",
                    f"Category {category or rule_index + 1!r} needs an extension.",
                )
            )
        for extension_index, raw_extension in enumerate(rule.extensions):
            field = f"rules.{rule_index}.extensions.{extension_index}"
            extension = normalize_extension(raw_extension)
            if extension is None:
                issues.append(
                    ConfigurationValidationIssue(
                        field,
                        "INVALID_EXTENSION",
                        f"Invalid extension: {raw_extension!r}",
                    )
                )
                continue
            owner = extension_owners.get(extension)
            if owner is not None:
                owner_category, owner_field = owner
                if owner_category == category:
                    message = (
                        f"Extension {extension} is repeated in category {category}."
                    )
                    code = "DUPLICATE_EXTENSION"
                else:
                    message = (
                        f"Extension {extension} is assigned to both "
                        f"{owner_category} and {category}."
                    )
                    code = "EXTENSION_CATEGORY_CONFLICT"
                issues.append(
                    ConfigurationValidationIssue(
                        field,
                        code,
                        message,
                        (owner_field, field),
                    )
                )
            else:
                extension_owners[extension] = (category, field)
            normalized_extensions.append(extension)
        normalized_rules.append(
            ProductRule(
                FALLBACK_CATEGORY if category.casefold() == FALLBACK_CATEGORY else category,
                tuple(normalized_extensions),
            )
        )
    return tuple(normalized_rules), tuple(issues)


def configuration_revision(document: dict) -> str:
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _sync_directory(path: Path) -> None:
    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
