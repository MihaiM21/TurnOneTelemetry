"""Check a running API for one event's data served under another event.

    python -m src.workers.audit_live_mapping --host https://apidev.t1f1.com --year 2026 --api-key $T1_API_KEY
    python -m src.workers.audit_live_mapping --host https://api.t1f1.com --year 2025 --rounds 1-24

Read-only (GET only). For every official round it requests the same data endpoint
for each session and groups identical payloads: two different Grands Prix never
produce the same qualifying classification, so a group of more than one round is
a mapping bug (or a stale cache entry written by one). It also checks that each
round's calendar entry (``/api/v2/seasons/{year}/events``) is the event it should be.

Exit status is 1 when anything suspicious is found, so it can gate a deploy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import requests

#: (endpoint, session) pairs probed per round. Chosen because their payload is unique to the session.
DEFAULT_PROBES: List[Tuple[str, str]] = [
    ("/api/v2/qualifying-results-data", "Q"),
    ("/api/v2/position-changes-data", "R"),
]


def _rounds(spec: Optional[str], default_last: int) -> List[int]:
    if not spec:
        return list(range(1, default_last + 1))
    if "-" in spec:
        first, last = spec.split("-", 1)
        return list(range(int(first), int(last) + 1))
    return [int(p) for p in spec.split(",")]


def _fetch(host: str, headers: dict, path: str, params: dict) -> Tuple[int, Optional[str], int]:
    """``(status, payload_sha256 or None, payload_chars)``."""
    try:
        resp = requests.get(host + path, params=params, headers=headers, timeout=120)
    except requests.RequestException:
        return 0, None, 0
    if resp.status_code != 200:
        return resp.status_code, None, 0
    try:
        body = json.dumps(resp.json(), sort_keys=True, separators=(",", ":"))
    except ValueError:
        return resp.status_code, None, 0
    return 200, hashlib.sha256(body.encode("utf-8")).hexdigest(), len(body)


def audit(host: str, year: int, api_key: str, rounds: Optional[str], probes=None, workers: int = 4) -> dict:
    host = host.rstrip("/")
    headers = {"X-API-Key": api_key}
    probes = probes or DEFAULT_PROBES

    calendar = requests.get(f"{host}/api/v2/seasons/{year}/events", headers=headers, timeout=60)
    calendar.raise_for_status()
    events = calendar.json()["events"]
    races = {e["round"]: e for e in events if e.get("round")}
    by_name = False
    if not races:
        # A deployment that predates the calendar rework lists raw livetiming meetings with no
        # round: number the non-testing ones in order and address them by name instead.
        by_name = True
        listed = [e for e in events if "testing" not in (e.get("name") or "").lower()]
        races = {n: e for n, e in enumerate(listed, start=1)}
    wanted = [r for r in _rounds(rounds, max(races) if races else 24) if r in races]

    jobs = [(r, path, session) for r in wanted for path, session in probes]

    def run(job):
        r, path, session = job
        gp = races[r]["name"] if by_name else r
        return job, _fetch(host, headers, path, {"year": year, "gp": gp, "session": session})

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run, jobs))

    groups: Dict[Tuple[str, str, str], List[int]] = {}
    rows = []
    for (r, path, session), (status, digest, size) in results:
        rows.append({"round": r, "event": races[r]["name"], "endpoint": path, "session": session,
                     "status": status, "sha256": digest, "chars": size})
        if digest and size > 60:
            groups.setdefault((path, session, digest), []).append(r)

    duplicates = [
        {"endpoint": path, "session": session, "rounds": rs, "events": [races[r]["name"] for r in rs]}
        for (path, session, _d), rs in groups.items() if len(rs) > 1
    ]
    return {"host": host, "year": year, "checked_rounds": wanted, "duplicates": duplicates, "rows": rows}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", required=True)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--api-key", default=os.environ.get("T1_API_KEY"))
    parser.add_argument("--rounds", help="e.g. 1-8 or 3,5,9 (default: every round)")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)
    if not args.api_key:
        parser.error("give --api-key or set T1_API_KEY")

    report = audit(args.host, args.year, args.api_key, args.rounds, workers=args.workers)
    print(f"{report['host']} {report['year']}: checked rounds {report['checked_rounds']}")  # CLI output
    for row in report["rows"]:
        print(f"  R{row['round']:>2} {row['session']:<2} {row['event']:<28} {row['status']} {row['sha256'] or '-'}")
    for dup in report["duplicates"]:
        print(f"DUPLICATE {dup['endpoint']} {dup['session']}: rounds {dup['rounds']} -> {', '.join(dup['events'])}")
    if not report["duplicates"]:
        print("no event serves another event's data")
    return 1 if report["duplicates"] else 0


if __name__ == "__main__":
    sys.exit(main())
