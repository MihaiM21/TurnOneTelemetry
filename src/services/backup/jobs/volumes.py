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
        tar.extractall(target_parent)
    return target_parent
