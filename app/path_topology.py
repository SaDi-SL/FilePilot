from collections.abc import Callable, Iterable
from pathlib import Path


class UnsafePathTopologyError(ValueError):
    pass


PathResolver = Callable[[str], Path]


def canonicalize_path(path: str | Path, resolver: PathResolver | None = None) -> Path:
    resolved_input = resolver(str(path)) if resolver is not None else Path(path)
    return Path(resolved_input).resolve(strict=False)


def is_path_within(path: str | Path, root: str | Path) -> bool:
    return canonicalize_path(path).is_relative_to(canonicalize_path(root))


def get_organized_root(
    config: dict,
    resolver: PathResolver | None = None,
) -> Path:
    organized_root = config.get("organized_base_folder")
    if organized_root is None:
        try:
            organized_root = Path(config["destination_folders"]["others"]).parent
        except (KeyError, TypeError) as error:
            raise UnsafePathTopologyError(
                "Configuration does not define an organized root"
            ) from error
    return canonicalize_path(organized_root, resolver)


def get_configured_watch_roots(
    config: dict,
    resolver: PathResolver | None = None,
) -> list[Path]:
    if "watch_folders" in config:
        folders = config.get("watch_folders")
        if not isinstance(folders, list):
            raise UnsafePathTopologyError("watch_folders must be a list")
        try:
            paths = [folder["path"] for folder in folders]
        except (KeyError, TypeError) as error:
            raise UnsafePathTopologyError(
                "Every watch folder must define a path"
            ) from error
    else:
        source_folder = config.get("source_folder")
        paths = [source_folder] if source_folder else []

    return [canonicalize_path(path, resolver) for path in paths]


def validate_watch_root(
    watch_root: str | Path,
    organized_root: str | Path,
) -> tuple[Path, Path]:
    watch_path = canonicalize_path(watch_root)
    organized_path = canonicalize_path(organized_root)

    if watch_path == organized_path:
        raise UnsafePathTopologyError(
            f"Watch root and organized root must be different: {watch_path}"
        )
    if organized_path.is_relative_to(watch_path):
        raise UnsafePathTopologyError(
            f"Organized root cannot be inside watch root: {organized_path} "
            f"is inside {watch_path}"
        )
    if watch_path.is_relative_to(organized_path):
        raise UnsafePathTopologyError(
            f"Watch root cannot be inside organized root: {watch_path} "
            f"is inside {organized_path}"
        )

    return watch_path, organized_path


def validate_path_topology(
    watch_roots: Iterable[str | Path],
    organized_root: str | Path,
) -> tuple[list[Path], Path]:
    organized_path = canonicalize_path(organized_root)
    validated_roots = []
    for watch_root in watch_roots:
        watch_path, _ = validate_watch_root(watch_root, organized_path)
        validated_roots.append(watch_path)
    return validated_roots, organized_path


def validate_configured_topology(
    config: dict,
    resolver: PathResolver | None = None,
) -> tuple[list[Path], Path]:
    organized_root = get_organized_root(config, resolver)
    watch_roots = get_configured_watch_roots(config, resolver)
    return validate_path_topology(watch_roots, organized_root)
