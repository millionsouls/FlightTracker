"""Tests for the bundled TLE cache used when the runtime cache is unavailable."""

from __future__ import annotations

import json
import time

from sgp4.api import Satrec

import utilities.tle_manager as tle_manager

_TLE = (
    "ISS (ZARYA)",
    "1 25544U 98067A   26282.82140207  .00006479  00000+0  12652-3 0  9998",
    "2 25544  51.6314  92.7969 0006775 242.6225 117.4075 15.48793003589526",
)


def _cache_payload(fetched_at: float, tle: tuple[str, str, str]) -> dict:
    norad_id = tle_manager.norad_id_from_line1(tle[1])
    return {
        "version": tle_manager.CACHE_VERSION,
        "blocked_until": 0.0,
        "entries": {
            str(norad_id): {"fetched_at": fetched_at, "tle": list(tle)},
        },
        "retry": {},
    }


def test_bundled_cache_matches_all_tle_text_files():
    cache = json.loads(tle_manager.BACKUP_TLE_CACHE_PATH.read_text())
    source_files = list(tle_manager.BACKUP_TLE_CACHE_PATH.parent.glob("*.txt"))

    assert len(source_files) == len(cache["entries"]) == 8
    for path in source_files:
        name, line1, line2 = [
            line.strip() for line in path.read_text().splitlines() if line.strip()
        ]
        norad_id = tle_manager.norad_id_from_line1(line1)

        assert cache["entries"][str(norad_id)]["tle"] == [name, line1, line2]
        assert Satrec.twoline2rv(line1, line2).satnum == norad_id


def test_load_cache_uses_bundled_cache_when_runtime_cache_is_missing(
    tmp_path, monkeypatch
):
    backup_path = tmp_path / "backup.json"
    runtime_path = tmp_path / "runtime.json"
    backup_path.write_text(json.dumps(_cache_payload(time.time(), _TLE)))
    monkeypatch.setattr(tle_manager, "BACKUP_TLE_CACHE_PATH", backup_path)
    monkeypatch.setattr(tle_manager, "TLE_CACHE_PATH", runtime_path)

    entries, retry, blocked_until = tle_manager.load_cache()

    assert entries[25544].tle == _TLE
    assert retry == {}
    assert blocked_until == 0.0


def test_cache_only_mode_is_disabled_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv(tle_manager.CACHE_ONLY_ENV, raising=False)
    assert tle_manager.TLEManager().cache_only is False

    monkeypatch.setenv(tle_manager.CACHE_ONLY_ENV, "1")
    assert tle_manager.TLEManager().cache_only is True


def test_successful_fetch_updates_the_runtime_cache(tmp_path, monkeypatch):
    backup_path = tmp_path / "backup.json"
    runtime_path = tmp_path / "runtime.json"
    backup_path.write_text(
        json.dumps(_cache_payload(time.time() - tle_manager.TLE_CACHE_TTL - 1, _TLE))
    )
    monkeypatch.setattr(tle_manager, "BACKUP_TLE_CACHE_PATH", backup_path)
    monkeypatch.setattr(tle_manager, "TLE_CACHE_PATH", runtime_path)

    class _StubConfig:
        satellite_norad_ids = [25544]

        @classmethod
        def instance(cls):
            return cls()

    fresh_tle = (
        "ISS (ZARYA)",
        "1 25544U 98067A   26282.90000000  .00006479  00000+0  12652-3 0  9998",
        "2 25544  51.6314  92.7969 0006775 242.6225 117.4075 15.48793003589526",
    )
    monkeypatch.setattr(tle_manager, "Config", _StubConfig)
    monkeypatch.setattr(tle_manager, "fetch_tle", lambda norad_id: fresh_tle)

    manager = tle_manager.TLEManager(cache_only=False)
    manager.prime_from_disk()
    manager.refresh()

    persisted = json.loads(runtime_path.read_text())
    assert persisted["entries"]["25544"]["tle"] == list(fresh_tle)
    assert tle_manager.load_cache()[0][25544].tle == fresh_tle

    backup = json.loads(backup_path.read_text())
    assert backup["entries"]["25544"]["tle"] == list(fresh_tle)
    assert backup["retry"] == {}
    assert backup["blocked_until"] == 0.0
