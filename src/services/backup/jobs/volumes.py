"""Optional tar.gz backup of the ``outputs/`` directory.

Skipped by default. ``cache/`` (FastF1 raw downloads, regenerable) and
``logs/`` are never included.
"""
from __future__ import annotations

import tarfile
from pathlib import Path

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


def dump(dst: Path) -> Path | None:
    src = Path(settings.output_dir)
    if not src.exists():
        logger.info(f"volumes: output_dir {src} does not exist, skipping")
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"volumes: tar.gz {src} -> {dst}")
    with tarfile.open(dst, "w:gz", compresslevel=6) as tar:
        tar.add(src, arcname=src.name)
    return dst


def restore(src: Path, target_parent: Path | None = None) -> Path:
    if not src.exists():
        raise FileNotFoundError(src)
    target_parent = target_parent or Path(settings.output_dir).parent
    target_parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"volumes: extract {src} -> {target_parent}")
    with tarfile.open(src, "r:gz") as tar:
        # Hardened against tarslip (CVE-2007-4559); see _extract_safely.
        _extract_safely(tar, target_parent)
    return target_parent


class OutsideDestinationError(ValueError):
    """A tar member resolved outside the extraction destination."""


def _vet_members(tar: tarfile.TarFile, dest: Path) -> None:
    """Reject any member that would escape ``dest`` or is not a regular file.

    Runs on every Python version, before extraction, so the safety argument
    does not depend on the interpreter's ``filter=`` support.
    """
    dest_resolved = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if dest_resolved != target and dest_resolved not in target.parents:
            raise OutsideDestinationError(
                f"Refusing tar member outside destination: {member.name!r}"
            )
        if member.issym() or member.islnk() or member.isdev():
            raise OutsideDestinationError(
                f"Refusing non-regular tar member: {member.name!r}"
            )


def _extract_safely(tar: tarfile.TarFile, dest: Path) -> None:
    """``extractall`` hardened against tarslip (CVE-2007-4559).

    Members are vetted by :func:`_vet_members` first, so the extraction below
    can only touch paths inside ``dest``. On 3.12+ we additionally pass
    ``filter="data"`` as defence in depth.
    """
    _vet_members(tar, dest)
    try:
        # nosec B202 - members vetted by _vet_members() immediately above.
        tar.extractall(dest, filter="data")  # nosec B202
    except TypeError:
        # Python < 3.12 has no `filter=`; the vetting above is the guarantee.
        # nosec B202 - members vetted by _vet_members() immediately above.
        tar.extractall(dest)  # nosec B202
