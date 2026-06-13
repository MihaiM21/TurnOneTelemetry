"""Prometheus metrics for the backup subsystem.

Defined in their own module so the registry stays singleton and we don't
duplicate ``Counter`` registration on hot-reload.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

BACKUP_LAST_SUCCESS = Gauge(
    "t1api_backup_last_success_timestamp",
    "Unix timestamp of the last successful backup",
    ["kind"],
)

BACKUP_DURATION = Histogram(
    "t1api_backup_duration_seconds",
    "Backup duration in seconds",
    ["kind"],
    buckets=[1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600],
)

BACKUP_SIZE = Histogram(
    "t1api_backup_size_bytes",
    "Backup artifact size in bytes (ciphertext)",
    ["kind"],
    buckets=[1e5, 1e6, 1e7, 1e8, 1e9, 1e10],
)

BACKUP_FAILURES = Counter(
    "t1api_backup_failures_total",
    "Total number of backup failures",
    ["kind"],
)
