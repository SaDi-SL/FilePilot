"""Bounded, read-only directory snapshots for the desktop browser."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrowserEntry:
    path: Path
    is_directory: bool
    size: int
    modified: float
    file_count: int = 0
    folder_count: int = 0
    partial: bool = False


@dataclass(frozen=True)
class BrowserListing:
    path: Path
    entries: tuple[BrowserEntry, ...]
    partial: bool = False


def browse_directory(root: Path, path: Path, limit: int = 1000) -> BrowserListing:
    root = root.resolve(strict=True)
    path = path.resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_dir():
        raise ValueError("This folder is outside the organized destination.")
    entries = []
    partial = False
    count_budget = 10000
    with os.scandir(path) as children:
        for child in children:
            if len(entries) >= limit:
                partial = True
                break
            try:
                # Linked locations are excluded to keep navigation within the destination.
                if child.is_symlink() or Path(child.path).resolve().parent != path:
                    continue
                info = child.stat(follow_symlinks=False)
                is_dir = child.is_dir(follow_symlinks=False)
                if not is_dir and not child.is_file(follow_symlinks=False):
                    continue
                files = folders = size = 0
                incomplete = False
                if is_dir:
                    try:
                        with os.scandir(child.path) as contents:
                            for member in contents:
                                if count_budget <= 0:
                                    incomplete = True
                                    break
                                count_budget -= 1
                                if member.is_symlink():
                                    continue
                                if member.is_dir(follow_symlinks=False):
                                    folders += 1
                                elif member.is_file(follow_symlinks=False):
                                    files += 1
                                    size += member.stat(follow_symlinks=False).st_size
                    except OSError:
                        incomplete = True
                else:
                    size = info.st_size
                entries.append(BrowserEntry(Path(child.path), is_dir, size, info.st_mtime, files, folders, incomplete))
            except OSError:
                partial = True
    return BrowserListing(path, tuple(entries), partial)


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return ""
