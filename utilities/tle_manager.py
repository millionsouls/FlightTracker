"""
TLEManager - fetch and cache TLE data from CelesTrak.

Resolves NORAD catalog IDs (integers) to (name, line1, line2) tuples using
CelesTrak's GP endpoint:

    https://celestrak.org/NORAD/elements/gp.php?CATNR={id}&FORMAT=TLE

Results are cached to disk per satellite and considered fresh for
TLE_CACHE_TTL seconds (3 days).  The cache is always consulted first; a
request is only sent for satellites that are missing from the cache or whose
cached entry has expired.  A refresh only replaces the entries that were
successfully retrieved - everything else is left untouched.

Bundled TLE data in assets/tle/tle_cache.json seeds the cache when no usable
runtime cache exists, so pass prediction can start during an initial outage.

Retry delays after failed fetches are persisted alongside the TLEs, so a
restart does not trigger an immediate retry storm.

Policy compliance: any HTTP response other than 200 from CelesTrak is logged
as CRITICAL and suspends ALL requests for BLOCK_DURATION (24 hours).  The
suspension is persisted, so restarts do not bypass it.  Thread-safe.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple

from setup.configuration import CONFIG_PATH, ROOT_PATH, Config, migrate_legacy_json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Set TLE_CACHE_ONLY=1 in the environment to start the manager in cache-only
# debug mode (no network requests at all).
CACHE_ONLY_ENV = "TLE_CACHE_ONLY"

TLE_CACHE_TTL = 3 * 86400  # 3 days
TLE_CACHE_PATH = migrate_legacy_json(
    ROOT_PATH / "tle_cache.json", CONFIG_PATH.parent / "tle_cache.json"
)
BACKUP_TLE_CACHE_PATH = ROOT_PATH / "assets" / "tle" / "tle_cache.json"
CACHE_VERSION = 2
HTTP_TIMEOUT = 15

# When a refresh fails for a satellite we still have a (stale) entry for,
# wait this long before trying that satellite again.
STALE_RETRY_DELAY = 14400  # 4 hours

# Backoff for a satellite that failed and has NO cached entry: starts at
# 1 minute, doubles each failure, capped at 1 hour.  Cleared on success.
BACKOFF_MIN = 60.0
BACKOFF_MAX = 3600.0

# After any non-200 HTTP response, make no requests at all for this long.
BLOCK_DURATION = 24 * 3600.0

# Persisted retry times further in the future than this are treated as
# corrupt / clock-skewed and clamped (e.g. the Pi booted with a wrong clock).
MAX_RETRY_DELAY = max(STALE_RETRY_DELAY, BACKOFF_MAX)

# An expired entry is still served (with a warning) while refreshes keep
# retrying - slightly stale TLEs beat having none during a CelesTrak outage.
# Entries older than this are discarded.
STALE_SERVE_MAX = 30 * 86400

# Upper bound on how long the refresh loop sleeps, so config changes and
# retry deadlines are noticed reasonably quickly.
MAX_SLEEP = 300.0

GP_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={catnr}&FORMAT=TLE"


class CacheEntry(NamedTuple):
    fetched_at: float
    tle: tuple[str, str, str]


class RetryState(NamedTuple):
    retry_at: float  # wall-clock time before which we won't retry
    backoff: float  # current exponential backoff (0 for stale-entry retries)


class UnexpectedStatus(Exception):
    """CelesTrak answered with an HTTP status other than 200."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


def fetch_tle(norad_id: int) -> tuple[str, str, str] | None:
    """Fetch a single TLE by NORAD catalog number.

    Returns (name, l1, l2), or None on a network error / unparseable body.
    Raises UnexpectedStatus if the server responds with anything but 200.
    """
    url = GP_URL.format(catnr=norad_id)
    status = 0
    body = ""
    try:
        req = urllib.request.Request(
            url,
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            status = resp.status
            if status == 200:
                body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # urllib raises for 4xx/5xx; this is a real HTTP response.
        raise UnexpectedStatus(exc.code) from exc
    except Exception as exc:
        # Timeouts, DNS failures, connection resets: no HTTP response at all.
        logger.error("HTTP error fetching TLE for NORAD %d: %s", norad_id, exc)
        return None

    if status != 200:  # e.g. 204 - urllib doesn't raise for other 2xx
        raise UnexpectedStatus(status)

    lines = [line.rstrip() for line in body.splitlines() if line.strip()]
    if len(lines) >= 3 and lines[1].startswith("1 ") and lines[2].startswith("2 "):
        return lines[0].strip(), lines[1], lines[2]

    logger.warning("No valid TLE in response for NORAD %d", norad_id)
    return None


def norad_id_from_line1(line1: str) -> int | None:
    """Extract the catalog number from TLE line 1 (columns 3-7)."""
    try:
        return int(line1[2:7])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------


def _load_cache_file(
    path: Path,
) -> tuple[dict[int, CacheEntry], dict[int, RetryState], float]:
    """Parse one current or legacy TLE cache file."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}, {}, 0.0
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning("Unable to read TLE cache %s: %s", path, exc)
        return {}, {}, 0.0
    if not isinstance(data, dict):
        logger.warning("Skipping malformed TLE cache file %s", path)
        return {}, {}, 0.0

    entries: dict[int, CacheEntry] = {}
    retry: dict[int, RetryState] = {}
    blocked_until = 0.0

    if isinstance(data.get("entries"), dict):
        for key, item in data["entries"].items():
            try:
                name, l1, l2 = item["tle"]
                entries[int(key)] = CacheEntry(
                    float(item["fetched_at"]), (name, l1, l2)
                )
            except Exception:
                logger.warning("Skipping malformed TLE cache entry %r", key)

        if isinstance(data.get("retry"), dict):
            for key, item in data["retry"].items():
                try:
                    retry[int(key)] = RetryState(
                        float(item["retry_at"]), float(item.get("backoff", 0.0))
                    )
                except Exception:
                    logger.warning("Skipping malformed TLE retry state %r", key)

        try:
            blocked_until = float(data.get("blocked_until", 0.0))
        except (TypeError, ValueError):
            blocked_until = 0.0

    elif "timestamp" in data and isinstance(data.get("tles"), list):
        try:
            ts = float(data["timestamp"])
        except (TypeError, ValueError):
            return {}, {}, 0.0
        for raw in data["tles"]:
            try:
                name, l1, l2 = raw
            except Exception:
                continue
            nid = norad_id_from_line1(l1)
            if nid is not None:
                entries[nid] = CacheEntry(ts, (name, l1, l2))

    return entries, retry, blocked_until


def load_cache() -> tuple[dict[int, CacheEntry], dict[int, RetryState], float]:
    """Load runtime cache, using the bundled cache for missing TLEs.

    Returns ({}, {}, 0.0) if the file is missing or unreadable.  Also
    understands the legacy format ({"timestamp": ..., "tles": [...]}) and
    converts it, using the catalog number embedded in each TLE.  Files
    written before retry/block persistence existed simply lack those keys.
    The runtime cache wins unless a bundled entry has a newer fetch time.
    """
    backup_entries, _backup_retry, _backup_blocked_until = _load_cache_file(
        BACKUP_TLE_CACHE_PATH
    )
    entries, retry, blocked_until = _load_cache_file(TLE_CACHE_PATH)
    backup_count = 0
    for nid, entry in backup_entries.items():
        if nid not in entries or entry.fetched_at > entries[nid].fetched_at:
            entries[nid] = entry
            backup_count += 1
    if backup_count:
        logger.info(
            "Loaded bundled TLE cache entries for %d satellite(s)",
            backup_count,
        )
    return entries, retry, blocked_until


def save_cache(
    entries: dict[int, CacheEntry],
    retry: dict[int, RetryState],
    blocked_until: float = 0.0,
) -> None:
    """Persist TLEs, retry state and request block atomically."""
    payload = {
        "version": CACHE_VERSION,
        "blocked_until": blocked_until,
        "entries": {
            str(nid): {"fetched_at": e.fetched_at, "tle": list(e.tle)}
            for nid, e in entries.items()
        },
        "retry": {
            str(nid): {"retry_at": r.retry_at, "backoff": r.backoff}
            for nid, r in retry.items()
        },
    }
    _write_cache_file(TLE_CACHE_PATH, payload, "TLE cache")


def _write_cache_file(path: Path, payload: dict, label: str) -> None:
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(path)
    except Exception as exc:
        logger.warning("%s write failed: %s", label, exc)


def _update_backup_cache(
    fetched: dict[int, tuple[str, str, str]], fetched_at: float
) -> None:
    """Refresh bundled seed entries after successful fetches.

    Retry and request-block state stays in the device-local runtime cache.
    """
    if not fetched:
        return

    entries, _retry, _blocked_until = _load_cache_file(BACKUP_TLE_CACHE_PATH)
    if BACKUP_TLE_CACHE_PATH.exists() and not entries:
        logger.warning("Bundled TLE cache was not updated because it is unreadable")
        return

    for nid, tle in fetched.items():
        entries[nid] = CacheEntry(fetched_at, tle)

    payload = {
        "version": CACHE_VERSION,
        "blocked_until": 0.0,
        "entries": {
            str(nid): {"fetched_at": entry.fetched_at, "tle": list(entry.tle)}
            for nid, entry in entries.items()
        },
        "retry": {},
    }
    _write_cache_file(BACKUP_TLE_CACHE_PATH, payload, "TLE backup cache")


# ---------------------------------------------------------------------------
# TLEManager
# ---------------------------------------------------------------------------


class TLEManager:
    """
    Manages TLE fetching, caching, and serving for SatelliteScene.

    Usage:
        mgr = TLEManager()
        mgr.start()
        tles = mgr.get()   # blocks briefly on first call, then instant
    """

    def __init__(self, cache_only: bool | None = None):
        self.lock = threading.Lock()
        self.entries: dict[int, CacheEntry] = {}
        self.retry: dict[int, RetryState] = {}
        self.blocked_until: float = 0.0
        self.force = False
        self.ready = threading.Event()
        self.wake = threading.Event()
        # Debug: when True, the cache is served as-is and CelesTrak is never
        # contacted.  Defaults to the TLE_CACHE_ONLY environment variable.
        if cache_only is None:
            cache_only = os.environ.get(CACHE_ONLY_ENV, "").lower() in (
                "1",
                "true",
                "yes",
            )
        self.cache_only = cache_only
        if self.cache_only:
            logger.warning("TLEManager started in CACHE-ONLY debug mode - no fetches")

    # -- public API ---------------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self.run_loop, daemon=True, name="tle-manager").start()

    def get(self, timeout: float = 30.0) -> list[tuple[str, str, str]]:
        """Block until the cache has been checked, then return the TLE list."""
        self.ready.wait(timeout=timeout)
        return self.snapshot()

    def try_get(self) -> list[tuple[str, str, str]] | None:
        """Non-blocking: return cached TLEs if ready, else None."""
        if not self.ready.is_set():
            return None
        return self.snapshot()

    def invalidate(self, force: bool = False) -> None:
        """Re-evaluate the cache on the next cycle (e.g. after config change).

        Retry delays are cleared.  Newly added satellites are fetched;
        satellites that are cached and still fresh are NOT re-requested.
        Pass force=True to refetch everything.  Existing cached TLEs are kept
        as a fallback, so a failed forced refresh loses nothing.

        This never lifts a policy block: if CelesTrak suspended us for 24h,
        no request is made until that period ends.
        """
        with self.lock:
            self.retry.clear()
            if force:
                self.force = True
        self.ready.clear()
        self.wake.set()

    def set_cache_only(self, enabled: bool) -> None:
        """Debug: toggle cache-only mode at runtime.

        While enabled, no request is ever sent; whatever is on disk / in
        memory is served, however old.  Disabling resumes normal refreshing
        (retry delays and any CelesTrak block still apply).
        """
        self.cache_only = enabled
        logger.warning(
            "TLE cache-only debug mode %s", "ENABLED" if enabled else "disabled"
        )
        if enabled:
            self.ready.set()  # don't make get() wait for a fetch that won't happen
        self.wake.set()
    # -- helpers ------------------------------------------------------------

    @staticmethod
    def configured_ids() -> list[int]:
        ids = Config.instance().satellite_norad_ids or []
        return list(dict.fromkeys(ids))  # de-duplicate, keep order

    def snapshot(self) -> list[tuple[str, str, str]]:
        """TLEs for the currently configured satellites, in config order."""
        ids = self.configured_ids()
        with self.lock:
            return [self.entries[i].tle for i in ids if i in self.entries]

    def due_ids(self, ids: list[int], now: float, force: bool = False) -> list[int]:
        """IDs that are missing/expired in the cache and not in a retry delay.

        Caller must hold self.lock.
        """
        due = []
        for nid in ids:
            entry = self.entries.get(nid)
            expired = (
                force or entry is None or now - entry.fetched_at >= TLE_CACHE_TTL
            )
            state = self.retry.get(nid)
            if expired and (state is None or now >= state.retry_at):
                due.append(nid)
        return due

    def seconds_until_next_check(self) -> float:
        ids = self.configured_ids()
        now = time.time()
        waits = []
        with self.lock:
            if self.blocked_until > now:
                return max(1.0, min(self.blocked_until - now, MAX_SLEEP))
            for nid in ids:
                entry = self.entries.get(nid)
                expiry = entry.fetched_at + TLE_CACHE_TTL if entry else 0.0
                state = self.retry.get(nid)
                retry_at = state.retry_at if state else 0.0
                waits.append(max(expiry, retry_at) - now)
        if not waits:
            return MAX_SLEEP
        return max(1.0, min(min(waits), MAX_SLEEP))

    # -- refresh loop -------------------------------------------------------

    def run_loop(self) -> None:
        self.prime_from_disk()

        while True:
            self.refresh()
            self.wake.wait(timeout=self.seconds_until_next_check())
            self.wake.clear()

    def prime_from_disk(self) -> None:
        """Load the disk cache, retry state and block at startup.

        Expired entries are still served (up to STALE_SERVE_MAX) so pass
        prediction keeps working during a CelesTrak outage; refresh() will
        try to replace them right after, honouring any persisted retry delay
        and any active request block.
        """
        cached, retry, blocked_until = load_cache()
        if not cached and not retry and not blocked_until:
            return

        now = time.time()
        usable: dict[int, CacheEntry] = {}
        for nid, entry in cached.items():
            age = now - entry.fetched_at
            if age >= STALE_SERVE_MAX:
                logger.warning(
                    "Cached TLE for NORAD %d too old to serve (%.1f days) - "
                    "will fetch fresh",
                    nid,
                    age / 86400.0,
                )
                continue
            if age >= TLE_CACHE_TTL:
                logger.warning(
                    "Serving stale TLE for NORAD %d (%.1f days old) - "
                    "refresh will be attempted",
                    nid,
                    age / 86400.0,
                )
            usable[nid] = entry

        # Clamp absurd retry times (clock changes / corrupt file) and drop
        # state that no longer matters.
        restored: dict[int, RetryState] = {}
        for nid, state in retry.items():
            entry = usable.get(nid)
            if entry is not None and now - entry.fetched_at < TLE_CACHE_TTL:
                continue  # entry is fresh; retry state is obsolete
            restored[nid] = RetryState(
                min(state.retry_at, now + MAX_RETRY_DELAY),
                min(state.backoff, BACKOFF_MAX),
            )
            if state.retry_at > now:
                logger.info(
                    "Restored TLE retry delay for NORAD %d (%.0fs remaining)",
                    nid,
                    restored[nid].retry_at - now,
                )

        # Restore the policy block (clamped so a corrupt value can't lock us
        # out for longer than one block period).
        blocked_until = min(blocked_until, now + BLOCK_DURATION)
        if blocked_until > now:
            logger.warning(
                "CelesTrak requests are suspended for another %.1f hours "
                "(earlier non-200 response)",
                (blocked_until - now) / 3600.0,
            )
        else:
            blocked_until = 0.0

        with self.lock:
            self.entries = usable
            self.retry = restored
            self.blocked_until = blocked_until

        # Serve immediately if every configured satellite is covered by the
        # cache (fresh or stale); otherwise the first refresh() sets ready.
        ids = self.configured_ids()
        if ids and all(i in usable for i in ids):
            self.ready.set()

    def refresh(self) -> None:
        """Fetch only the satellites that are missing or expired in the cache."""
        ids = self.configured_ids()
        if not ids:
            self.ready.set()
            return

        if self.cache_only:
            with self.lock:
                missing = [i for i in ids if i not in self.entries]
            if missing:
                logger.warning(
                    "Cache-only mode: no cached TLE for NORAD %s (not fetching)",
                    ", ".join(map(str, missing)),
                )
            else:
                logger.debug("Cache-only mode: serving cached TLEs, no fetch")
            self.ready.set()
            return

        # 1. Check the block and the cache first - no request unless allowed
        #    and something is due.
        with self.lock:
            now = time.time()
            blocked = now < self.blocked_until
            remaining = self.blocked_until - now
            
            if not blocked:
                force, self.force = self.force, False
                due = self.due_ids(ids, now, force)
        if blocked:
            logger.debug(
                "TLE requests suspended for another %.1f hours", remaining / 3600.0
            )
            self.ready.set()
            return
        if not due:
            logger.debug("No TLE fetch needed for %d satellite(s)", len(ids))
            self.ready.set()
            return

        # 2. Fetch only what's due (outside the lock - network I/O).  A
        #    non-200 response aborts the whole batch immediately.
        logger.debug("TLE refresh starting for %d/%d satellite(s)", len(due), len(ids))
        fetched: dict[int, tuple[str, str, str]] = {}
        failed: list[int] = []
        bad_status: int | None = None
        for nid in due:
            if self.cache_only:
                logger.warning("Cache-only mode enabled mid-refresh - aborting fetches")
                break
            try:
                tle = fetch_tle(nid)
            except UnexpectedStatus as exc:
                bad_status = exc.status
                logger.critical(
                    "CelesTrak returned HTTP %d for NORAD %d (expected 200) - "
                    "suspending ALL TLE requests for %d hours",
                    exc.status,
                    nid,
                    int(BLOCK_DURATION // 3600),
                )
                break  # remaining satellites are not attempted
            if tle:
                logger.debug("TLE fetched: %s (NORAD %d)", tle[0], nid)
                fetched[nid] = tle
            else:
                failed.append(nid)

        # 3. Merge: update only retrieved entries, record retry state for the
        #    rest, then persist everything.
        now = time.time()
        with self.lock:
            for nid, tle in fetched.items():
                self.entries[nid] = CacheEntry(now, tle)
                self.retry.pop(nid, None)

            for nid in failed:
                if nid in self.entries:
                    # Keep serving the stale entry; retry in a few hours.
                    self.retry[nid] = RetryState(now + STALE_RETRY_DELAY, 0.0)
                    logger.warning(
                        "TLE refresh failed for NORAD %d - keeping cached copy; "
                        "next retry in %d hours",
                        nid,
                        STALE_RETRY_DELAY // 3600,
                    )
                else:
                    # Nothing cached for this satellite - exponential backoff.
                    prev = self.retry.get(nid, RetryState(0.0, 0.0)).backoff
                    delay = BACKOFF_MIN if prev <= 0.0 else min(prev * 2.0, BACKOFF_MAX)
                    self.retry[nid] = RetryState(now + delay, delay)
                    logger.warning(
                        "TLE refresh failed for NORAD %d - no cached data; "
                        "next retry in %.0fs",
                        nid,
                        delay,
                    )

            # Start (or clear an expired) request block.
            self.blocked_until = now + BLOCK_DURATION if bad_status is not None else 0.0

            # Drop entries too old to ever serve, and retry state for
            # satellites that are no longer configured.
            for nid in [
                n
                for n, e in self.entries.items()
                if now - e.fetched_at >= STALE_SERVE_MAX
            ]:
                del self.entries[nid]
            wanted = set(ids)
            for nid in [n for n in self.retry if n not in wanted]:
                del self.retry[nid]

            entries_copy = dict(self.entries)
            retry_copy = dict(self.retry)
            blocked_until = self.blocked_until

        save_cache(entries_copy, retry_copy, blocked_until)
        _update_backup_cache(fetched, now)
        if bad_status is None:
            logger.info(
                "TLE refresh complete - %d/%d due satellite(s) updated",
                len(fetched),
                len(due),
            )
        else:
            logger.info(
                "TLE refresh aborted after HTTP %d - %d/%d due satellite(s) "
                "updated before the block",
                bad_status,
                len(fetched),
                len(due),
            )

        self.ready.set()

def debug_cached_tles(
    norad_ids: list[int] | None = None,
) -> list[tuple[str, str, str]]:
    """Debug helper: return TLEs straight from the disk cache.

    Makes no network requests, starts no threads and writes nothing.  Expired
    entries are included (and flagged in the log), and the STALE_SERVE_MAX
    cutoff is ignored, so you can inspect exactly what is on disk.
    Defaults to the currently configured satellites.
    """
    entries, _retry, blocked_until = load_cache()
    ids = norad_ids if norad_ids is not None else TLEManager.configured_ids()
    now = time.time()

    if blocked_until > now:
        logger.info(
            "CelesTrak block active for another %.1f hours",
            (blocked_until - now) / 3600.0,
        )

    result: list[tuple[str, str, str]] = []
    for nid in ids:
        entry = entries.get(nid)
        if entry is None:
            logger.warning("NORAD %d: not in cache", nid)
            continue
        age_days = (now - entry.fetched_at) / 86400.0
        state = "fresh" if now - entry.fetched_at < TLE_CACHE_TTL else "EXPIRED"
        logger.info(
            "NORAD %d: %s - %s, %.1f days old", nid, entry.tle[0], state, age_days
        )
        result.append(entry.tle)
    return result