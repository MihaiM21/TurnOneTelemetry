"""livetiming auth / f1-proxy failures must surface as outages, never as "not published".

Since 14-15 Oct 2026 livetiming's /static/<year>/ archive answers 401 without an
F1 TV token; the f1-proxy Worker (infra/cloudflare/f1-proxy) holds the token and
answers 403 + X-Proxy-Error to a wrong X-Proxy-Key. Mapped like livetiming's own
403, that would read as "no data for this season".
"""
import pytest
import responses

from src.core.config import settings
from src.core.exceptions import SessionNotFoundError, UpstreamUnavailableError
from src.ingestion import static_client as sc
from src.ingestion.static_client import F1StaticClient

ORIGIN = "https://livetiming.formula1.com/static"
PROXY = "https://f1-proxy.example.workers.dev"


@pytest.fixture(autouse=True)
def _fresh_index_cache():
    sc.clear_index_cache()
    yield
    sc.clear_index_cache()


@pytest.fixture
def proxied(monkeypatch):
    monkeypatch.setattr(settings, "f1_proxy_base_url", PROXY)
    monkeypatch.setattr(settings, "f1_proxy_key", "shared-key")


@responses.activate
def test_bare_origin_401_explains_the_archive_needs_a_token():
    responses.add(responses.GET, f"{ORIGIN}/2026/Index.json", status=401)
    with pytest.raises(UpstreamUnavailableError) as exc:
        F1StaticClient().fetch_season_index(2026)
    assert "F1 TV token" in exc.value.reason
    assert "F1_PROXY_KEY" in exc.value.reason


@responses.activate
def test_proxy_key_is_sent_when_proxy_configured(proxied):
    responses.add(responses.GET, f"{PROXY}/2026/Index.json", json={"Meetings": []}, status=200)
    F1StaticClient().fetch_season_index(2026)
    assert responses.calls[0].request.headers["X-Proxy-Key"] == "shared-key"


@responses.activate
def test_proxy_key_never_sent_to_origin(monkeypatch):
    monkeypatch.setattr(settings, "f1_proxy_base_url", None)
    monkeypatch.setattr(settings, "f1_proxy_key", "shared-key")
    responses.add(responses.GET, f"{ORIGIN}/2026/Index.json", json={"Meetings": []}, status=200)
    F1StaticClient().fetch_season_index(2026)
    assert "X-Proxy-Key" not in responses.calls[0].request.headers


@responses.activate
def test_rejected_token_reports_expiry(proxied):
    responses.add(
        responses.GET, f"{PROXY}/2026/Index.json", status=401,
        headers={"X-Upstream-Auth": "rejected", "X-F1TV-Token-Expires": "2026-11-20T10:00:00.000Z"},
    )
    with pytest.raises(UpstreamUnavailableError) as exc:
        F1StaticClient().fetch_season_index(2026)
    assert "rejected" in exc.value.reason
    assert "2026-11-20T10:00:00.000Z" in exc.value.reason


@responses.activate
def test_missing_token_on_worker(proxied):
    responses.add(responses.GET, f"{PROXY}/2026/Index.json", status=401, headers={"X-Upstream-Auth": "missing"})
    with pytest.raises(UpstreamUnavailableError) as exc:
        F1StaticClient().fetch_season_index(2026)
    assert "no F1 TV token" in exc.value.reason


@responses.activate
def test_wrong_proxy_key_is_an_outage_not_a_missing_season(proxied):
    responses.add(responses.GET, f"{PROXY}/2026/Index.json", status=403, headers={"X-Proxy-Error": "forbidden"})
    with pytest.raises(UpstreamUnavailableError) as exc:
        F1StaticClient().fetch_season_index(2026)
    assert "F1_PROXY_KEY" in exc.value.reason


@responses.activate
def test_livetiming_own_403_still_means_not_published(proxied):
    # 2022's index answers a plain 403 upstream; the Worker passes it through untagged.
    responses.add(responses.GET, f"{PROXY}/2022/Index.json", status=403)
    with pytest.raises(SessionNotFoundError):
        F1StaticClient().fetch_season_index(2022)


@responses.activate
def test_stream_parsers_surface_401_clearly(proxied):
    url = f"{PROXY}/2026/x/y/CarData.z.jsonStream"
    responses.add(responses.GET, url, status=401, headers={"X-Upstream-Auth": "rejected"})
    client = F1StaticClient()
    for parse in (client.parse_compressed_stream, client.parse_jsonstream_simple, client.parse_jsonstream):
        with pytest.raises(UpstreamUnavailableError, match="rejected"):
            parse(url)
