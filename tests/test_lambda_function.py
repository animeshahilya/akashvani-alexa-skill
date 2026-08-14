import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lambda_function
from lambda_function import find_station, fetch_stations


STATIONS = [
    {"name": "Mirchi FM", "stream_url": "https://example.com/mirchi.mp3"},
    {"name": "Radio Mirchi Delhi", "stream_url": "https://example.com/mirchidelhi.mp3"},
    {"name": "AIR Vividh Bharati", "stream_url": "https://example.com/vb.mp3"},
]


def test_exact_match_case_insensitive():
    result = find_station(STATIONS, "mirchi fm")
    assert result["name"] == "Mirchi FM"


def test_exact_match_preferred_over_partial():
    # "Mirchi FM" is both an exact match and a substring of "Radio Mirchi Delhi"-style names;
    # the exact match must win.
    result = find_station(STATIONS, "Mirchi FM")
    assert result["name"] == "Mirchi FM"


def test_partial_match_fallback():
    result = find_station(STATIONS, "Vividh Bharati")
    assert result["name"] == "AIR Vividh Bharati"


def test_no_match_returns_none():
    assert find_station(STATIONS, "Nonexistent Station") is None


def test_empty_requested_name_does_not_match_everything():
    # Regression test: "" is a substring of every string in Python, so the old partial-match
    # loop would have silently matched the first station in the list for an empty request.
    assert find_station(STATIONS, "") is None
    assert find_station(STATIONS, "   ") is None


def test_none_requested_name():
    assert find_station(STATIONS, None) is None


def test_empty_station_list():
    assert find_station([], "Mirchi FM") is None


def test_fetch_stations_caches_and_skips_refetch(monkeypatch):
    lambda_function._stations_cache["data"] = None
    lambda_function._stations_cache["fetched_at"] = 0.0

    call_count = {"n": 0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'[{"name": "Test Station", "stream_url": "https://example.com/x.mp3"}]'

    def fake_urlopen(req, timeout=5):
        call_count["n"] += 1
        return FakeResponse()

    monkeypatch.setattr(lambda_function.urllib.request, "urlopen", fake_urlopen)

    first = fetch_stations()
    second = fetch_stations()

    assert call_count["n"] == 1, "second call within the TTL window should use the cache, not refetch"
    assert first == second
    assert first[0]["name"] == "Test Station"


def test_fetch_stations_retries_then_serves_stale_cache_on_total_failure(monkeypatch):
    lambda_function._stations_cache["data"] = [{"name": "Stale Station", "stream_url": "https://example.com/stale.mp3"}]
    lambda_function._stations_cache["fetched_at"] = 0.0  # force past TTL

    def always_fails(req, timeout=5):
        raise TimeoutError("simulated network failure")

    monkeypatch.setattr(lambda_function.urllib.request, "urlopen", always_fails)

    result = fetch_stations()

    assert result == [{"name": "Stale Station", "stream_url": "https://example.com/stale.mp3"}]
