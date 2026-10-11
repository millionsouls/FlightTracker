"""Tests for utilities/tle_manager.py - disk-cache priming and holdoff.

The manager must keep serving a stale TLE cache (pass prediction degrades
gracefully over days) while a provider outage makes refreshes fail; the
refresh loop continues on its own backoff schedule.
"""

from __future__ import annotations

import json
import logging
import time

import pytest

import utilities.tle_manager as tle_manager

_TLE = ("ISS (ZARYA)", "1 25544U 98067A   ...", "2 25544  51.6 ...")


@pytest.fixture
def cache_path(tmp_path, monkeypatch):
    path = tmp_path / "tle_cache.json"
    monkeypatch.setattr(tle_manager, "TLE_CACHE_PATH", path)

    class _StubConfig:
        satellite_norad_ids = [25544]
        satellite_tle_source = "celestrak"
        n2yo_api_key = ""

        @classmethod
        def instance(cls):
            return cls()

    monkeypatch.setattr(tle_manager, "Config", _StubConfig)
    return path


def _write_cache(path, age_seconds):
    path.write_text(
        json.dumps(
            {
                "timestamp": time.time() - age_seconds,
                "tles": [list(_TLE)],
            }
        )
    )


class TestPrimeFromDisk:
    def test_missing_cache_is_a_noop(self, cache_path):
        mgr = tle_manager.TLEManager()
        mgr.prime_from_disk()

        assert mgr.snapshot() == []
        assert not mgr.ready.is_set()

    def test_fresh_cache_serves_immediately(self, cache_path, caplog):
        _write_cache(cache_path, age_seconds=100)
        mgr = tle_manager.TLEManager()

        mgr.prime_from_disk()

        assert mgr.ready.is_set()
        assert mgr.snapshot() == [_TLE]
        assert "stale" not in caplog.text.lower()

    def test_stale_cache_is_served_with_warning(self, cache_path, caplog):
        """Outage resilience: a days-old cache still predicts passes."""
        _write_cache(cache_path, age_seconds=5 * 86400)
        mgr = tle_manager.TLEManager()

        with caplog.at_level(logging.DEBUG):
            mgr.prime_from_disk()

        assert mgr.ready.is_set()
        assert mgr.snapshot() == [_TLE]
        assert "stale" in caplog.text.lower()

    def test_ancient_cache_is_discarded(self, cache_path, caplog):
        _write_cache(cache_path, age_seconds=40 * 86400)
        mgr = tle_manager.TLEManager()

        mgr.prime_from_disk()

        assert mgr.snapshot() == []
        assert not mgr.ready.is_set()
        assert "too old to serve" in caplog.text


class TestFailedRefreshHoldoff:
    def test_stale_fallback_bumps_retry_by_four_hours(
        self, cache_path, monkeypatch, caplog
    ):
        """With stale data in memory, a failed refresh advances the retry
        clock (4h) instead of hammering the provider; the retry is persisted."""
        _write_cache(cache_path, age_seconds=tle_manager.TLE_CACHE_TTL + 100)
        mgr = tle_manager.TLEManager()
        mgr.prime_from_disk()

        monkeypatch.setattr(tle_manager, "fetch_tle", lambda norad_id: None)

        before = time.time()
        mgr.refresh()

        retry = mgr.retry[25544]
        assert mgr.ready.is_set()
        assert retry.retry_at - before == pytest.approx(tle_manager.STALE_RETRY_DELAY, abs=2)
        assert retry.backoff == 0.0
        assert "keeping cached copy" in caplog.text

        persisted = json.loads(cache_path.read_text())
        assert persisted["retry"]["25544"]["retry_at"] == retry.retry_at
        assert persisted["entries"]["25544"]["tle"] == list(_TLE)

    def test_no_cached_data_backs_off(self, cache_path, monkeypatch, caplog):
        """First-ever refresh failure backs off exponentially, serving
        nothing until data arrives."""
        mgr = tle_manager.TLEManager()

        monkeypatch.setattr(tle_manager, "fetch_tle", lambda norad_id: None)

        with caplog.at_level(logging.DEBUG):
            mgr.refresh()
            first = mgr.retry[25544]
            assert first.backoff == tle_manager.BACKOFF_MIN

            mgr.retry[25544] = tle_manager.RetryState(time.time() - 1, first.backoff)
            mgr.refresh()
            second = mgr.retry[25544]
            assert second.backoff == tle_manager.BACKOFF_MIN * 2

        assert mgr.ready.is_set()
        assert mgr.snapshot() == []
