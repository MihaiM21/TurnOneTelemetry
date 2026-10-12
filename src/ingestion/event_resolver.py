"""
Canonical resolver for "give me the event matching this round / key / name".

V2 endpoints accept ``gp`` as ``Union[int, str]``. Every caller resolves it here,
against the season calendar (:mod:`src.ingestion.calendar`), so they all agree on
which event a request means and on the canonical :class:`EventInfo`.

Resolution order (first match wins):

1. Integer (or all-digit string): the **official championship round** (the same
   number FastF1/V1 uses -- pre-season testing and cancelled events have none).
   Otherwise the livetiming meeting ``Key``.
2. Exact match, tier by tier -- the first tier with any candidate decides:

   a. names: GP name, its stem (``"Bahrain"`` for ``"Bahrain Grand Prix"``),
      official name, code, aliases (``"USA"``, ``"Sepang"``, ``"Madrid"`` ...);
   b. place: circuit and host city;
   c. country.

3. Unique substring match.

Within a tier, active races beat cancelled ones (the 2026 Bahrain GP that was run
in Malaysia wins over the Sakhir round that was cancelled) and races beat
pre-season testing. If several candidates are still left the request is
ambiguous (``"Spain"`` in 2026) and the candidates are listed rather than one
being picked arbitrarily.

A cancelled event resolves to a 404 that says so (and names its replacement)
unless the caller passes ``allow_cancelled=True``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

from src.core.exceptions import SessionNotFoundError
from src.core.logging import get_logger
from src.ingestion.calendar import (
    KIND_RACE,
    KIND_TESTING,
    STATUS_CANCELLED,
    fold,
)
from src.ingestion.reference import get_season_calendar

logger = get_logger(__name__)

_GP_SUFFIXES = (" grand prix", " gp")


@dataclass(frozen=True)
class EventInfo:
    """Canonical event identity used by every V2 caller."""

    year: int
    round_nr: int                      # official round; 0 for pre-season testing
    name: str
    official_name: str = ""
    code: str = ""
    key: Optional[int] = None          # livetiming meeting Key (None until livetiming lists it)
    circuit: str = ""
    country: str = ""
    circuit_key: Optional[int] = None  # livetiming Circuit.Key == stored layout id
    location: str = ""
    kind: str = KIND_RACE
    status: str = "scheduled"
    gp_id: Optional[str] = None
    replaced_by: Optional[str] = None
    reason: str = ""

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
            "circuit_key": self.circuit_key,
            "location": self.location,
            "kind": self.kind,
            "status": self.status,
            "gp_id": self.gp_id,
        }


def _fold(value: Any) -> str:
    return fold(value)


def _kind(event: dict) -> str:
    return event.get("kind") or KIND_RACE


def _status(event: dict) -> str:
    return event.get("status") or "scheduled"


def _name(event: dict) -> str:
    return event.get("grandPrix") or event.get("name") or ""


def _stem(name: str) -> str:
    lowered = name.lower()
    for suffix in _GP_SUFFIXES:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _event_to_info(year: int, event: dict) -> EventInfo:
    rnd = event.get("round")
    return EventInfo(
        year=year,
        round_nr=rnd if isinstance(rnd, int) else 0,
        name=_name(event),
        official_name=event.get("officialName") or "",
        code=event.get("code") or "",
        key=event.get("key"),
        circuit=event.get("circuit") or "",
        country=event.get("country") or "",
        circuit_key=event.get("circuitKey") if isinstance(event.get("circuitKey"), int) else None,
        location=event.get("location") or "",
        kind=_kind(event),
        status=_status(event),
        gp_id=event.get("gp_id"),
        replaced_by=event.get("replacedBy"),
        reason=event.get("reason") or "",
    )


class EventResolver:
    """Resolve a free-form V2 identifier to a canonical :class:`EventInfo`."""

    def __init__(self, year: int):
        self.year = int(year)
        self._events: list[dict] = get_season_calendar(self.year)

    @staticmethod
    def _record_outcome(outcome: str) -> None:
        try:
            from src.core.observability.monitoring import T1API_EVENT_RESOLUTION
            T1API_EVENT_RESOLUTION.labels(outcome=outcome).inc()
        except Exception:
            pass

    # ------------------------------------------------------------------ views

    def _races(self) -> list[dict]:
        return [e for e in self._events if _kind(e) == KIND_RACE and _status(e) != STATUS_CANCELLED]

    @property
    def valid_rounds(self) -> list[int]:
        return sorted({e["round"] for e in self._races() if isinstance(e.get("round"), int)})

    @property
    def candidate_names(self) -> list[str]:
        return [_name(e) for e in self._races()]

    # ------------------------------------------------------------------ API

    def resolve(self, identifier: Union[int, str], *, allow_cancelled: bool = False) -> EventInfo:
        """Return the matching event or raise :class:`SessionNotFoundError`."""
        try:
            event = self._resolve_event(identifier)
            if _status(event) == STATUS_CANCELLED and not allow_cancelled:
                self._record_outcome("cancelled")
                raise SessionNotFoundError(
                    year=self.year, gp=identifier, reason=self._cancelled_reason(event),
                    valid_rounds=self.valid_rounds,
                    suggestions=[event["replacedBy"]] if event.get("replacedBy") else None,
                )
            return _event_to_info(self.year, event)
        except SessionNotFoundError:
            self._record_outcome("not_found")
            raise

    def _cancelled_reason(self, event: dict) -> str:
        text = f"The {self.year} {_name(event)} ({event.get('location') or event.get('country')}) was cancelled"
        if event.get("reason"):
            text += f": {event['reason']}"
        else:
            text += "."
        if event.get("replacedBy"):
            text += f" It was replaced by the {event['replacedBy']} (key {event.get('replacedByKey')})."
        return text

    # ------------------------------------------------------------------ core

    def _resolve_event(self, identifier: Union[int, str]) -> dict:
        as_int = self._coerce_int(identifier)
        if as_int is not None:
            event = self._resolve_int(as_int, original=identifier)
            self._record_outcome("round" if _kind(event) == KIND_RACE and event.get("round") == as_int else "key")
            return event

        text = str(identifier or "").strip()
        if not text:
            raise SessionNotFoundError(year=self.year, gp=identifier, reason="Empty event identifier.")

        folded = _fold(text)
        for tier in (self._tier_names, self._tier_places, self._tier_country):
            matches = tier(folded)
            if matches:
                event = self._pick(text, matches)
                self._record_outcome("exact")
                return event

        matches = self._fuzzy_matches(folded)
        if matches:
            event = self._pick(text, matches)
            self._record_outcome("fuzzy")
            return event

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

    def _resolve_int(self, num: int, *, original: Any) -> dict:
        # Official round first: races only, never testing or cancelled events,
        # whose ``round`` is None.
        for event in self._events:
            if _kind(event) == KIND_RACE and _status(event) != STATUS_CANCELLED and event.get("round") == num:
                return event
        # Anything else is a livetiming meeting Key (testing included).
        for event in self._events:
            if event.get("key") is not None and event.get("key") == num:
                return event

        raise SessionNotFoundError(
            year=self.year, gp=original,
            reason=(
                f"Round/Key {num} not found in {self.year}. "
                f"Valid rounds: {', '.join(str(r) for r in self.valid_rounds) or '(none)'}."
            ),
            valid_rounds=self.valid_rounds,
        )

    # ------------------------------------------------------------------ tiers

    def _tier_names(self, folded: str) -> list[dict]:
        out = []
        for event in self._events:
            name = _name(event)
            fields = [name, _stem(name), event.get("officialName"), event.get("code"), event.get("curatedName")]
            fields += event.get("aliases") or []
            if any(_fold(f) == folded for f in fields if f):
                out.append(event)
        return out

    def _tier_places(self, folded: str) -> list[dict]:
        return [
            e for e in self._events
            if folded in (_fold(e.get("circuit")), _fold(e.get("location")))
        ]

    def _tier_country(self, folded: str) -> list[dict]:
        return [e for e in self._events if _fold(e.get("country")) == folded]

    def _fuzzy_matches(self, folded: str) -> list[dict]:
        if not folded:
            return []
        out = []
        for event in self._events:
            parts = [_name(event), event.get("officialName"), event.get("code"), event.get("circuit"),
                     event.get("location"), event.get("country")] + list(event.get("aliases") or [])
            blob = " ".join(_fold(p) for p in parts if p)
            tokens = {_fold(p) for p in parts if p}
            if folded in tokens or folded in blob:
                out.append(event)
        return out

    def _pick(self, text: str, matches: list[dict]) -> dict:
        """Collapse a tier's candidates: races beat testing, active beats cancelled."""
        pool = matches
        races = [e for e in pool if _kind(e) == KIND_RACE]
        if races:
            pool = races
        active = [e for e in pool if _status(e) != STATUS_CANCELLED]
        if active:
            pool = active
        if len(pool) == 1:
            return pool[0]
        candidates = [_name(e) + (f" ({e['location']})" if e.get("location") else "") for e in pool]
        raise SessionNotFoundError(
            year=self.year, gp=text,
            reason=(
                f"Identifier {text!r} matches multiple events in {self.year}: "
                f"{', '.join(candidates)}. Please be more specific (use a name, round number or key)."
            ),
            suggestions=[_name(e) for e in pool],
            valid_rounds=self.valid_rounds,
        )

    def _top_fuzzy_candidates(self, text: str, *, limit: int) -> list[str]:
        folded = _fold(text)
        if not folded:
            return []
        scored: list[tuple[int, str]] = []
        for event in self._races():
            name = _name(event)
            blob = _fold(name)
            score = sum(1 for a, b in zip(folded, blob) if a == b)
            if score > 0:
                scored.append((score, name))
        scored.sort(key=lambda p: p[0], reverse=True)
        return [name for _score, name in scored[:limit]]


def resolve_event(year: int, identifier: Union[int, str], *, allow_cancelled: bool = False) -> EventInfo:
    """Convenience wrapper for one-shot lookups."""
    return EventResolver(year).resolve(identifier, allow_cancelled=allow_cancelled)


__all__ = ["EventInfo", "EventResolver", "resolve_event", "KIND_TESTING"]
