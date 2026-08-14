import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lambda_function
from lambda_function import find_station, fetch_stations, _normalize_spoken_numbers


STATIONS = [
    {"name": "Mirchi FM", "stream_url": "https://example.com/mirchi.mp3"},
    {"name": "Radio Mirchi Delhi", "stream_url": "https://example.com/mirchidelhi.mp3"},
    {"name": "AIR Vividh Bharati", "stream_url": "https://example.com/vb.mp3"},
    {"name": "Fairfax Radio", "stream_url": "https://example.com/fairfax.mp3"},
    {"name": "24 FM", "stream_url": "https://example.com/24fm.mp3"},
    {"name": "Aapan Radio", "stream_url": "https://example.com/aapan.mp3"},
]


def test_exact_match_case_insensitive():
    station, options = find_station(STATIONS, "mirchi fm")
    assert station["name"] == "Mirchi FM"
    assert options == []


def test_exact_match_preferred_over_partial():
    station, options = find_station(STATIONS, "Mirchi FM")
    assert station["name"] == "Mirchi FM"
    assert options == []


def test_whole_word_match_fallback():
    station, options = find_station(STATIONS, "Vividh Bharati")
    assert station["name"] == "AIR Vividh Bharati"
    assert options == []


def test_short_query_does_not_match_as_raw_substring():
    # Regression: the old substring-based matcher would match "air" against "Fairfax Radio"
    # (character substring), which has nothing to do with AIR. Whole-word matching must not.
    station, options = find_station(STATIONS, "air")
    assert station is None or station["name"] != "Fairfax Radio"


def test_ambiguous_whole_word_matches_return_as_options():
    stations = [
        {"name": "Radio Mirchi Delhi", "stream_url": "https://example.com/a.mp3"},
        {"name": "Radio Mirchi Mumbai", "stream_url": "https://example.com/b.mp3"},
    ]
    station, options = find_station(stations, "Radio Mirchi")
    assert station is None
    assert len(options) == 2


def test_fuzzy_typo_returns_as_option_not_auto_selected():
    station, options = find_station(STATIONS, "Apan Radio")  # missing an "a"
    assert station is None
    assert any(o["name"] == "Aapan Radio" for o in options)


def test_spoken_number_normalization_matches_digit_station_name():
    station, options = find_station(STATIONS, "twenty four fm")
    assert station["name"] == "24 FM"
    assert options == []


def test_normalize_spoken_numbers_compound():
    assert _normalize_spoken_numbers("twenty four fm") == "24 fm"
    assert _normalize_spoken_numbers("nine") == "9"
    assert _normalize_spoken_numbers("mirchi fm") == "mirchi fm"


def test_no_match_returns_none_and_no_options():
    station, options = find_station(STATIONS, "Completely Made Up Station Xyz")
    assert station is None
    assert options == []


def test_empty_requested_name_does_not_match_everything():
    # Regression: "" is a substring of every string in Python, so the old partial-match
    # loop would have silently matched the first station in the list for an empty request.
    assert find_station(STATIONS, "") == (None, [])
    assert find_station(STATIONS, "   ") == (None, [])


def test_none_requested_name():
    assert find_station(STATIONS, None) == (None, [])


def test_empty_station_list():
    assert find_station([], "Mirchi FM") == (None, [])


def test_excluded_station_never_matches_exactly():
    excluded_name = next(iter(lambda_function.EXCLUDED_STATIONS))
    stations = [{"name": excluded_name, "stream_url": "https://example.com/dead.mp3"}]
    station, options = find_station(stations, excluded_name)
    assert station is None
    assert options == []


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
