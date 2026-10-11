"""Tests for the alternate N2YO TLE source."""

from __future__ import annotations

import json
import time
import urllib.error

import pytest

import utilities.tle_manager as tle_manager

_TLE = (
    "ISS (ZARYA)",
    "1 25544U 98067A   26282.82140207  .00006479  00000+0  12652-3 0  9998",
    "2 25544  51.6314  92.7969 0006775 242.6225 117.4075 15.48793003589526",
)


def _cache_payload(fetched_at: float) -> dict:
    return {
        "version": tle_manager.CACHE_VERSION,
        "blocked_until": 0.0,
        "entries": {
            "25544": {"fetched_at": fetched_at, "tle": list(_TLE)},
        },
        "retry": {},
    }


@pytest.fixture
def cache_paths(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime.json"
    backup = tmp_path / "backup.json"
    monkeypatch.setattr(tle_manager, "TLE_CACHE_PATH", runtime)
    monkeypatch.setattr(tle_manager, "BACKUP_TLE_CACHE_PATH", backup)
    return runtime, backup


def test_fetch_n2yo_tle_uses_requested_endpoint_and_parses_response(monkeypatch):
    captured = {}
    response_body = json.dumps(
        {
            "info": {"satname": "ISS (ZARYA)", "satid": 25544},
            "tle": f"{_TLE[1]}\r\n{_TLE[2]}",
        }
    ).encode()

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return response_body

    def _urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(tle_manager.urllib.request, "urlopen", _urlopen)

    assert tle_manager.fetch_n2yo_tle(25544, "api-key") == _TLE
    assert captured == {
        "url": "https://api.n2yo.com/rest/v1/satellite/tle/25544&apiKey=api-key",
        "timeout": tle_manager.HTTP_TIMEOUT,
    }


def test_fetch_n2yo_tle_rejects_a_different_norad_id(monkeypatch):
    body = json.dumps(
        {
            "info": {"satname": "Other satellite"},
            "tle": "1 25545U 98067A   26282.82140207  .00006479  00000+0  12652-3 0  9998\n"
            "2 25545  51.6314  92.7969 0006775 242.6225 117.4075 15.48793003589526",
        }
    ).encode()

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return body

    monkeypatch.setattr(
        tle_manager.urllib.request, "urlopen", lambda *_args, **_kwargs: _Response()
    )

    assert tle_manager.fetch_n2yo_tle(25544, "api-key") is None


def test_fetch_n2yo_tle_encodes_key_and_does_not_leak_it(monkeypatch):
    captured = {}

    def _urlopen(request, timeout):
        captured["url"] = request.full_url
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(tle_manager.urllib.request, "urlopen", _urlopen)

    with pytest.raises(tle_manager.NetworkError) as error:
        tle_manager.fetch_n2yo_tle(25544, "private&key")

    assert captured["url"].endswith("/25544&apiKey=private%26key")
    assert "private&key" not in str(error.value)


def test_switching_source_keeps_fresh_cache_without_request(cache_paths, monkeypatch):
    _runtime, backup = cache_paths
    backup.write_text(json.dumps(_cache_payload(time.time())))

    class _Config:
        satellite_norad_ids = [25544]
        satellite_tle_source = "n2yo"
        n2yo_api_key = "api-key"

        @classmethod
        def instance(cls):
            return cls()

    monkeypatch.setattr(tle_manager, "Config", _Config)
    monkeypatch.setattr(
        tle_manager,
        "fetch_n2yo_tle",
        lambda *_args: pytest.fail("fresh cache should not trigger a request"),
    )
    manager = tle_manager.TLEManager(cache_only=False)
    manager.prime_from_disk()

    manager.refresh()

    assert manager.snapshot() == [_TLE]
    assert manager.request_log == []


def test_expired_cache_uses_only_the_selected_n2yo_source(cache_paths, monkeypatch):
    runtime, backup = cache_paths
    backup.write_text(
        json.dumps(_cache_payload(time.time() - tle_manager.TLE_CACHE_TTL - 1))
    )
    fresh_tle = ("ISS from N2YO", _TLE[1], _TLE[2])
    calls = []

    class _Config:
        satellite_norad_ids = [25544]
        satellite_tle_source = "n2yo"
        n2yo_api_key = "configured-key"

        @classmethod
        def instance(cls):
            return cls()

    def _fetch(norad_id, api_key):
        calls.append((norad_id, api_key))
        return fresh_tle

    monkeypatch.setattr(tle_manager, "Config", _Config)
    monkeypatch.setattr(tle_manager, "fetch_n2yo_tle", _fetch)
    monkeypatch.setattr(
        tle_manager,
        "fetch_tle",
        lambda *_args: pytest.fail("CelesTrak must not be called when N2YO is selected"),
    )
    manager = tle_manager.TLEManager(cache_only=False)
    manager.prime_from_disk()

    manager.refresh()
    manager.refresh()

    assert calls == [(25544, "configured-key")]
    assert manager.snapshot() == [fresh_tle]
    assert len(manager.request_log) == 1
    assert json.loads(runtime.read_text())["entries"]["25544"]["tle"] == list(
        fresh_tle
    )


def test_missing_n2yo_key_keeps_stale_cache_and_skips_requests(
    cache_paths, monkeypatch, caplog
):
    runtime, backup = cache_paths
    backup.write_text(
        json.dumps(_cache_payload(time.time() - tle_manager.TLE_CACHE_TTL - 1))
    )

    class _Config:
        satellite_norad_ids = [25544]
        satellite_tle_source = "n2yo"
        n2yo_api_key = ""

        @classmethod
        def instance(cls):
            return cls()

    monkeypatch.setattr(tle_manager, "Config", _Config)
    monkeypatch.setattr(
        tle_manager,
        "fetch_n2yo_tle",
        lambda *_args: pytest.fail("N2YO must not be called without a key"),
    )
    manager = tle_manager.TLEManager(cache_only=False)
    manager.prime_from_disk()

    manager.refresh()

    assert manager.snapshot() == [_TLE]
    assert manager.request_log == []
    assert manager.retry[25544].retry_at > time.time()
    assert "N2YO is selected but no API key is configured" in caplog.text
    assert json.loads(runtime.read_text())["entries"]["25544"]["tle"] == list(_TLE)
