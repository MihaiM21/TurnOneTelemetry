"""
Canonical resolver for "give me the event matching this round / key / name".

V2 endpoints accept ``gp`` as ``Union[int, str]``. Resolving it consistently
used to live inside :meth:`F1StaticClient.get_event_info` mixed with HTTP
concerns, and a parallel — drifting — implementation existed in
``orchestrator_helpers.py``. This module centralizes the lookup so every V2
caller resolves identifiers the same way and produces the same canonical
:class:`EventInfo` shape.

Resolution order (first match wins):

1. Integer (or all-digit string) in ``[1, 30]`` → round number.
2. Integer outside that range → livetiming meeting ``Key``.
3. Case-insensitive exact match against ``code``, ``name``, ``official_name``,
   ``circuit``, ``country``.
4. Diacritic-stripped substring match. If multiple candidates match, raise
   :class:`SessionNotFoundError` with the candidates so the user can
   disambiguate rather than getting an arbitrary choice.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Union

from src.core.exceptions import SessionNotFoundError
from src.core.logging import get_logger
from src.ingestion.reference import get_season_events

logger = get_logger(__name__)

# Integers above this are treated as livetiming meeting Keys, not round numbers.
_MAX_ROUND_NUMBER = 30


@dataclass(frozen=True)
class EventInfo:
    """Canonical event identity used by every V2 caller."""

    year: int
    round_nr: int
    name: str
    official_name: str = ""
    code: str = ""
    key: Optional[int] = None
    circuit: str = ""
    country: str = ""

    def as_dict(self) -> dict:
        return {
            "year": self.year,
            "round_nr": self.round_nr,
            "name": self.name,
            "official_name": self.official_name,
            "code": self.code,
            "key": self.key,
            "circuit": self.circuit,
            "country": self.country,
        }


def _fold(value: Any) -> str:
    """Lowercase + strip diacritics + collapse non-alphanumerics to single spaces."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    out_chars = []
    prev_space = True
    for ch in text.lower():
        if ch.isalnum():
            out_chars.append(ch)
            prev_space = False
        elif not prev_space:
            out_chars.append(" ")
            prev_space = True
    return "".join(out_chars).strip()


def _event_to_info(year: int, idx: int, event: dict) -> EventInfo:
    return EventInfo(
        year=year,
        round_nr=event.get("round", idx + 1) if isinstance(event.get("round"), int) else idx + 1,
        name=event.get("grandPrix") or event.get("name") or "",
        official_name=event.get("officialName") or "",
        code=event.get("code") or "",
        key=event.get("key"),
        circuit=event.get("circuit") or "",
        country=event.get("country") or "",
    )


class EventResolver:
    """Resolve a free-form V2 identifier to a canonical :class:`EventInfo`."""

    def __init__(self, year: int):
        self.year = int(year)
        self._events: list[dict] = get_season_events(self.year)

    @staticmethod
    def _record_outcome(outcome: str) -> None:
        try:
            from src.core.observability.monitoring import T1API_EVENT_RESOLUTION
            T1API_EVENT_RESOLUTION.labels(outcome=outcome).inc()
        except Exception:
            pass

    @property
    def valid_rounds(self) -> list[int]:
        return list(range(1, len(self._events) + 1))

    @property
    def candidate_names(self) -> list[str]:
        return [e.get("grandPrix") or e.get("name") or "" for e in self._events]

    def resolve(self, identifier: Union[int, str]) -> EventInfo:
        """Return the matching event or raise :class:`SessionNotFoundError`."""
        try:
            return self._resolve_inner(identifier)
        except SessionNotFoundError:
            self._record_outcome("not_found")
            raise

    def _resolve_inner(self, identifier: Union[int, str]) -> EventInfo:
        # Integer (or digit string) path.
        as_int = self._coerce_int(identifier)
        if as_int is not None:
            info = self._resolve_int(as_int, original=identifier)
            self._record_outcome(
                "round" if 1 <= as_int <= min(_MAX_ROUND_NUMBER, len(self._events)) else "key"
            )
            return info

        # Strict string handling.
        text = str(identifier or "").strip()
        if not text:
            raise SessionNotFoundError(
                year=self.year, gp=identifier,
                reason="Empty event identifier.",
            )

        info = self._resolve_exact(text)
        if info is not None:
            self._record_outcome("exact")
            return info
        info = self._resolve_fuzzy(text)
        if info is not None:
            self._record_outcome("fuzzy")
            return info

        raise SessionNotFoundError(
            year=self.year, gp=identifier,
            reason=(
                f"No event in {self.year} matches {identifier!r}. "
                f"Try one of: {', '.join(self.candidate_names) or '(no events available)'}"
            ),
            suggestions=self._top_fuzzy_candidates(text, limit=3),
            valid_rounds=self.valid_rounds,
        )

    @staticmethod
    def _coerce_int(identifier: Any) -> Optional[int]:
        if isinstance(identifier, bool):
            return None
        if isinstance(identifier, int):
            return identifier
        if isinstance(identifier, str):
            stripped = identifier.strip()
            if stripped.isdigit():
                return int(stripped)
            if stripped.startswith("-") and stripped[1:].isdigit():
                return int(stripped)
        return None

    def _resolve_int(self, num: int, *, original: Any) -> EventInfo:
        if 1 <= num <= min(_MAX_ROUND_NUMBER, len(self._events)):
            return _event_to_info(self.year, num - 1, self._events[num - 1])

        # Treat larger integers as livetiming Keys.
        for idx, event in enumerate(self._events):
            if event.get("key") == num:
                return _event_to_info(self.year, idx, event)

        raise SessionNotFoundError(
            year=self.year, gp=original,
            reason=(
                f"Round/Key {num} not found in {self.year}. "
                f"Valid rounds: 1..{len(self._events)}."
            ),
            valid_rounds=self.valid_rounds,
        )

    def _resolve_exact(self, text: str) -> Optional[EventInfo]:
        folded = _fold(text)
        for idx, event in enumerate(self._events):
            haystacks = (
                event.get("code"),
                event.get("grandPrix"),
                event.get("name"),
                event.get("officialName"),
                event.get("circuit"),
                event.get("country"),
            )
            if any(_fold(h) == folded and folded for h in haystacks):
                return _event_to_info(self.year, idx, event)
        return None

    def _resolve_fuzzy(self, text: str) -> Optional[EventInfo]:
        matches = self._fuzzy_matches(text)
        if not matches:
            return None
        if len(matches) == 1:
            idx = matches[0]
            return _event_to_info(self.year, idx, self._events[idx])

        # Ambiguous — let the caller see the candidates.
        candidates = [
            self._events[i].get("grandPrix") or self._events[i].get("name") or f"round {i + 1}"
            for i in matches
        ]
        raise SessionNotFoundError(
            year=self.year, gp=text,
            reason=(
                f"Identifier {text!r} matches multiple events in {self.year}: "
                f"{', '.join(candidates)}. Please be more specific."
            ),
            suggestions=candidates,
        )

    def _fuzzy_matches(self, text: str) -> list[int]:
        folded = _fold(text)
        if not folded:
            return []
        out: list[int] = []
        for idx, event in enumerate(self._events):
            blob = " ".join(
                _fold(event.get(k, "")) for k in
                ("grandPrix", "name", "officialName", "code", "circuit", "country")
            )
            if folded in blob.split() or folded in blob:
                out.append(idx)
        return out

    def _top_fuzzy_candidates(self, text: str, *, limit: int) -> list[str]:
        # Cheap ranking: longer common-prefix between folded blob and folded input wins.
        folded = _fold(text)
        if not folded:
            return []
        scored: list[tuple[int, str]] = []
        for event in self._events:
            name = event.get("grandPrix") or event.get("name") or ""
            blob = _fold(name)
            score = sum(1 for ch_a, ch_b in zip(folded, blob) if ch_a == ch_b)
            if score > 0:
                scored.append((score, name))
        scored.sort(key=lambda p: p[0], reverse=True)
        return [name for _score, name in scored[:limit]]


def resolve_event(year: int, identifier: Union[int, str]) -> EventInfo:
    """Convenience wrapper for one-shot lookups."""
    return EventResolver(year).resolve(identifier)


__all__ = ["EventInfo", "EventResolver", "resolve_event"]
