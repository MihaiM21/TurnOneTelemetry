"""Dataset export package: builds a Hive-partitioned Parquet/JSONL dataset of F1 session
data (tables, telemetry, raw streams and corpus text) under an admin-managed export
directory, so downstream analysis and ML tooling can consume a versioned, resumable
snapshot without going through the live API.
"""
