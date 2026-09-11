"""Unit tests for src.core.security.rate_limiting."""
from unittest.mock import MagicMock

import pytest
from slowapi.wrappers import LimitGroup

from src.core.config import settings
from src.core.security import rate_limiting as rl


def _fake_request(api_key=None, ip="1.2.3.4"):
    req = MagicMock()
    req.headers = {"X-API-Key": api_key} if api_key else {}
    req.client = MagicMock()
    req.client.host = ip
    # slowapi.util.get_remote_address looks at request.client.host
    return req


def test_get_rate_limit_key_public_when_no_api_key():
    key = rl.get_rate_limit_key(_fake_request())
    assert key.startswith("public:")


def test_get_rate_limit_key_premium():
    key = rl.get_rate_limit_key(_fake_request(api_key="test-premium-key"))
    assert key == "premium:test-premium-key"


def test_get_rate_limit_key_standard():
    key = rl.get_rate_limit_key(_fake_request(api_key="test-standard-key"))
    assert key == "standard:test-standard-key"


def test_get_rate_limit_key_unknown_falls_back_to_public():
    key = rl.get_rate_limit_key(_fake_request(api_key="bogus-key"))
    assert key.startswith("public:")


def test_get_limiter_raises_before_init(monkeypatch):
    monkeypatch.setattr(rl, "_limiter_instance", None)
    with pytest.raises(RuntimeError):
        rl.get_limiter()


def test_init_limiter_idempotent_returns_instance(monkeypatch):
    monkeypatch.setattr(rl, "_limiter_instance", None)
    limiter = rl.init_limiter()
    assert limiter is not None
    assert rl.get_limiter() is limiter


def test_apply_tiered_limit_returns_callable(monkeypatch):
    monkeypatch.setattr(rl, "_limiter_instance", None)
    rl.init_limiter()
    assert callable(rl.apply_tiered_limit("public"))
    assert callable(rl.apply_tiered_limit("standard"))
    assert callable(rl.apply_tiered_limit("data"))


# ---------------------------------------------------------------------------
# Regression coverage for the "standard" branch hardcoding premium limits for
# everyone. `_tiered_limit_value` is the dynamic `limit_value` callable now
# passed to `limiter.limit()`; these tests prove it actually varies by tier
# rather than just asserting `apply_tiered_limit` returns *something* callable.
# ---------------------------------------------------------------------------

def test_tiered_limit_value_public():
    result = rl._tiered_limit_value("public:1.2.3.4")
    assert result == (
        f"{settings.rate_limit_public_per_minute}/minute;"
        f"{settings.rate_limit_public_per_hour}/hour"
    )


def test_tiered_limit_value_standard():
    result = rl._tiered_limit_value("standard:test-standard-key")
    assert result == (
        f"{settings.rate_limit_standard_per_minute}/minute;"
        f"{settings.rate_limit_standard_per_hour}/hour"
    )


def test_tiered_limit_value_premium():
    result = rl._tiered_limit_value("premium:test-premium-key")
    assert result == (
        f"{settings.rate_limit_premium_per_minute}/minute;"
        f"{settings.rate_limit_premium_per_hour}/hour"
    )


def test_tiered_limit_value_differs_between_public_and_premium():
    # The bug being fixed: previously every tier got the premium ceiling.
    assert rl._tiered_limit_value("public:1.2.3.4") != rl._tiered_limit_value(
        "premium:test-premium-key"
    )


@pytest.mark.parametrize(
    "api_key, expected_amount",
    [
        (None, settings.rate_limit_public_per_minute),
        ("test-standard-key", settings.rate_limit_standard_per_minute),
        ("test-premium-key", settings.rate_limit_premium_per_minute),
        ("bogus-unknown-key", settings.rate_limit_public_per_minute),
    ],
)
def test_apply_tiered_limit_end_to_end_via_slowapi(monkeypatch, api_key, expected_amount):
    """Drive the real slowapi `LimitGroup` machinery (the code path
    `limiter.limit()` uses internally) with `apply_tiered_limit("standard")`'s
    dynamic limit_value + the limiter's key_func, exactly as it runs in
    production. This proves a no-key request resolves to the public per-minute
    number and a premium key resolves to the premium number -- not merely that
    `apply_tiered_limit` returns a callable.
    """
    monkeypatch.setattr(rl, "_limiter_instance", None)
    limiter = rl.init_limiter()
    decorator_factory = rl.apply_tiered_limit("standard")

    # Recover the dynamic limit_value + key_func exactly as __limit_decorator
    # would build them, without needing a real ASGI route.
    @decorator_factory
    def _dummy_endpoint(request):  # pragma: no cover - never actually called
        return "ok"

    request = _fake_request(api_key=api_key)

    # The decorator stashes the LimitGroup(s) on the limiter's dynamic route
    # limits keyed by the wrapped function's qualified name.
    name = f"{_dummy_endpoint.__module__}.{_dummy_endpoint.__name__}"
    groups = limiter._dynamic_route_limits.get(name, [])
    assert groups, "expected apply_tiered_limit('standard') to register a dynamic limit"

    resolved_limits = []
    for group in groups:
        assert isinstance(group, LimitGroup)
        group.with_request(request)
        resolved_limits.extend(list(group))

    per_minute_limits = [lim for lim in resolved_limits if lim.limit.GRANULARITY.name == "minute"]
    assert len(per_minute_limits) == 1
    assert per_minute_limits[0].limit.amount == expected_amount
