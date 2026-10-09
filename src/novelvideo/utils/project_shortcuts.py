"""Human-friendly project directory links under ``DATA_ROOT/项目``.

The links are navigation aids only.  Project data remains in the canonical
output directory, and removing an aid must never recurse into that target.
"""

from __future__ import annotations

import locale
import os
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path

DELETED_SUFFIX = "（已删除）"
DirectoryLinkCreator = Callable[[Path, Path], None]


class ProjectShortcutCollisionError(RuntimeError):
    """Raised when a real filesystem entry occupies a managed link name."""


def _shortcut_root(data_root: str | Path | None = None) -> Path:
    if data_root is None:
        from novelvideo import config

        data_root = config.DATA_ROOT
    return Path(data_root) / "项目"


def _entry_stat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _is_directory_link(path: Path, entry_stat: os.stat_result) -> bool:
    if stat.S_ISLNK(entry_stat.st_mode):
        return True
    if os.name != "nt":
        return False
    attributes = int(getattr(entry_stat, "st_file_attributes", 0))
    if not attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    reparse_tag = getattr(entry_stat, "st_reparse_tag", None)
    supported_tags = {
        getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None),
        getattr(stat, "IO_REPARSE_TAG_SYMLINK", None),
    }
    supported_tags.discard(None)
    return reparse_tag is None or reparse_tag in supported_tags


def _unlink_directory_link(path: Path) -> None:
    entry_stat = _entry_stat(path)
    if entry_stat is None:
        return
    if not _is_directory_link(path, entry_stat):
        raise ProjectShortcutCollisionError(
            f"Refusing to remove non-link project shortcut entry: {path}"
        )
    if stat.S_ISLNK(entry_stat.st_mode):
        path.unlink()
        return
    # Windows directory junctions are directories to lstat(), but rmdir removes
    # only the reparse point.  shutil.rmtree must never be used here.
    os.rmdir(path)


def _create_directory_link(link: Path, target: Path) -> None:
    if os.name != "nt":
        link.symlink_to(target, target_is_directory=True)
        return

    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode == 0:
        return
    encoding = locale.getpreferredencoding(False) or "utf-8"
    detail = completed.stdout.decode(encoding, errors="replace").strip()
    raise OSError(
        completed.returncode,
        f"Failed to create project junction {link} -> {target}: {detail}",
    )


def _ensure_directory_link(
    link: Path,
    target: Path,
    *,
    link_creator: DirectoryLinkCreator,
) -> None:
    if not target.is_dir():
        raise FileNotFoundError(f"Project output directory does not exist: {target}")

    entry_stat = _entry_stat(link)
    if entry_stat is not None:
        if not _is_directory_link(link, entry_stat):
            raise ProjectShortcutCollisionError(
                f"Project shortcut path is occupied by a real entry: {link}"
            )
        try:
            if os.path.samefile(link, target):
                return
        except OSError:
            # Broken or unreadable links are safely replaceable because the
            # deletion below only unlinks the reparse point itself.
            pass
        _unlink_directory_link(link)

    link_creator(link, target)


def sync_project_shortcut(
    *,
    project_name: str,
    output_dir: str | Path,
    deleted: bool,
    data_root: str | Path | None = None,
    link_creator: DirectoryLinkCreator | None = None,
) -> Path:
    """Create the active/deleted project link and remove its stale peer."""

    root = _shortcut_root(data_root)
    root.mkdir(parents=True, exist_ok=True)
    target = Path(output_dir).resolve()
    active = root / project_name
    deleted_entry = root / f"{project_name}{DELETED_SUFFIX}"
    desired = deleted_entry if deleted else active
    stale = active if deleted else deleted_entry
    _ensure_directory_link(
        desired,
        target,
        link_creator=link_creator or _create_directory_link,
    )
    _unlink_directory_link(stale)
    return desired


def remove_project_shortcuts(
    project_name: str,
    *,
    data_root: str | Path | None = None,
) -> None:
    """Idempotently unlink both active and deleted project navigation aids."""

    root = _shortcut_root(data_root)
    _unlink_directory_link(root / project_name)
    _unlink_directory_link(root / f"{project_name}{DELETED_SUFFIX}")
