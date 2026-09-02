"""Output-directory helpers for generated plots.

Every V2 analysis module builds its output path as
``outputs/plots/{year}/{event}/{session}`` and calls in here. Because
``session`` originates as a request query parameter, this module is the sink
for user-controlled path segments — so containment is enforced here as well as
at the request edge (``src/api/deps.py``). Belt and braces: 13+ call sites
reach these functions, and a future one could forget to validate.
"""
from __future__ import annotations

import os
from pathlib import Path

from src.core.logging import get_logger

logger = get_logger(__name__)

#: All generated artifacts live under this root, relative to the process CWD.
#: Kept relative (and resolved lazily in :func:`plots_root`) because callers
#: build relative paths for ``savefig`` and tests ``chdir`` into a tmp dir --
#: resolving once at import would pin the root to whatever the CWD happened to
#: be when this module was first imported.
PLOTS_ROOT_RELATIVE = Path("outputs/plots")


def plots_root() -> Path:
    """The output root, resolved against the *current* working directory."""
    return PLOTS_ROOT_RELATIVE.resolve()


class UnsafeOutputPath(ValueError):
    """Raised when a requested output path would escape ``the plots root."""


def resolve_within(name: str, base: Path | None = None) -> Path:
    """Resolve ``base/name`` and refuse to leave ``base``.

    Guards against ``..`` traversal, absolute-path injection and (on Windows)
    drive-relative paths. Symlinks are resolved before the check, so a symlink
    inside the tree cannot be used to hop out of it either.
    """
    base = (base or plots_root()).resolve()
    candidate = (base / name).resolve()
    if candidate != base and base not in candidate.parents:
        raise UnsafeOutputPath(f"Refusing to write outside {base}: {name!r}")
    return candidate


def createFolderForPlots(name):
    """Create ``outputs/plots/<name>``, refusing to escape that root."""
    path = resolve_within(name)
    os.makedirs(path, exist_ok=True)
    logger.debug("Created plot folder %s", path)
    return str(path)


def checkForFolder(name):
    """Ensure ``outputs/plots/<name>`` exists.

    Previously this probed ``../../outputs/plots/<name>`` while
    ``createFolderForPlots`` wrote to ``outputs/plots/<name>`` — two different
    directories, so the existence check never matched and the folder was
    re-created on every call. Both now agree on one root.
    """
    path = resolve_within(name)
    if path.is_dir():
        logger.debug("Plot folder already exists: %s", path)
        return str(path)
    return createFolderForPlots(name)


def checkForFile(original_path, name):
    """Return the path if the file exists, else the string ``"NULL"``.

    The ``"NULL"`` sentinel is preserved because callers compare against it.
    """
    path = os.path.join(original_path, name)
    if os.path.isfile(path):
        logger.debug("File already exists: %s", path)
        return path
    return "NULL"
