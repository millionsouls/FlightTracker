"""
TLEManager - fetch and cache TLE data from CelesTrak or N2YO.

Resolves NORAD catalog IDs (integers) to (name, line1, line2) tuples.
Exactly one provider is active at a time (config: satellite_tle_source):

    celestrak: https://celestrak.org/NORAD/elements/gp.php?CATNR={id}&FORMAT=TLE
    n2yo:      https://api.n2yo.com/rest/v1/satellite/tle/{id}&apiKey=...

Results are cached to disk per satellite and considered fresh for
TLE_CACHE_TTL seconds (3 days).  The cache is always consulted first; a
request is only sent for satellites that are missing from the cache or whose
cached entry has expired.  A refresh only replaces the entries that were
successfully retrieved - everything else is left untouched.

Bundled TLE data in assets/tle/tle_cache.json seeds the cache when no usable
runtime cache exists, so pass prediction can start during an initial outage.

Request limits (all persisted, so restarts cannot bypass them):
  * Rolling DAILY_REQUEST_BUDGET attempts per BUDGET_WINDOW, any provider.
  * N2YO only: the API reports its own usage counter (info.transactionscount).
    Once it reaches N2YO_TX_STOP, N2YO requests pause for N2YO_PAUSE.
  * Any non-200 HTTP response suspends all requests for BLOCK_DURATION.
  * A rejected N2YO key holds N2YO requests until the key is changed.

Retry delays after failed fetches are persisted alongside the TLEs, so a
restart does not trigger an immediate retry storm.  Thread-safe.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
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

# "No data for this ID" -> check again at most once a day.
NOT_FOUND_RETRY = 86400.0

# Persisted retry times further in the future than this are treated as
# corrupt / clock-skewed and clamped (e.g. the Pi booted with a wrong clock).
MAX_RETRY_DELAY = max(STALE_RETRY_DELAY, BACKOFF_MAX, NOT_FOUND_RETRY)

# An expired entry is still served (with a warning) while refreshes keep
# retrying - slightly stale TLEs beat having none during a provider outage.
# Entries older than this are discarded.
STALE_SERVE_MAX = 30 * 86400

# Upper bound on how long the refresh loop sleeps, so config changes and
# retry deadlines are noticed reasonably quickly.
MAX_SLEEP = 300.0

# Self-imposed ceiling on request attempts (any provider), leaving headroom
# under the hard 1000 limit.
DAILY_REQUEST_BUDGET = 800
BUDGET_WINDOW = 86400.0

# Persist the request log at least this often during a long batch, so a crash
# mid-batch cannot make the budget forget attempts.
REQUEST_LOG_SAVE_EVERY = 25

# Keep forced refreshes at least 2 hours apart per satellite.
MIN_REFETCH_INTERVAL = 2 * 3600.0

# Global cooldown after a network-level failure (no HTTP response at all).
NET_BACKOFF_MIN = 60.0
NET_BACKOFF_MAX = 900.0

# N2YO reports its own usage counter in info.transactionscount (transactions
# for this API key over a rolling 60 minutes).  Stop at this value (the limit
# is 1000) and pause for one full window so the counter can drain.
N2YO_TX_STOP = 900
N2YO_PAUSE = 3600.0

USER_AGENT = "your-app-name/1.0 (contact: you@example.com)"  # TODO: set a real one
GP_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={catnr}&FORMAT=TLE"
N2YO_TLE_URL = "https://api.n2yo.com/rest/v1/satellite/tle/{norad}&apiKey={api_key}"


class CacheEntry(NamedTuple):
    fetched_at: float
    tle: tuple[str, str, str]


class RetryState(NamedTuple):
    retry_at: float  # wall-clock time before which we won't retry
    backoff: float  # current exponential backoff (0 for stale-entry retries)


class UnexpectedStatus(Exception):
    """The selected TLE provider answered with an HTTP status other than 200."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


class NetworkError(Exception):
    """No HTTP response at all (DNS, timeout, reset)."""


class NoData(Exception):
    """The provider answered 200 but has no data for this NORAD ID."""


class ProviderRejected(Exception):
    """The provider refused the request (bad API key)."""


class RateLimited(Exception):
    """The provider reported that its rate limit / quota was exceeded."""


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


def norad_id_from_line1(line1: str) -> int | None:
    """Extract the catalog number from TLE line 1 (columns 3-7)."""
    try:
        return int(line1[2:7])
    except ValueError:
        return None


def fetch_tle(norad_id: int) -> tuple[str, str, str] | None:
    """Fetch one TLE from CelesTrak.

    Returns (name, l1, l2), or None for an unparseable body (transient).
    Raises UnexpectedStatus (non-200), NetworkError, or NoData.
    """
    url = GP_URL.format(catnr=norad_id)
    status = 0
    body = ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            status = resp.status
            if status == 200:
                body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise UnexpectedStatus(exc.code) from exc
    except Exception as exc:
        raise NetworkError(str(exc)) from exc

    if status != 200:  # e.g. 204 - urllib doesn't raise for other 2xx
        raise UnexpectedStatus(status)

    if body.lstrip().lower().startswith("no gp data found"):
        raise NoData(norad_id)

    lines = [line.rstrip() for line in body.splitlines() if line.strip()]
    if len(lines) >= 3 and lines[1].startswith("1 ") and lines[2].startswith("2 "):
        if norad_id_from_line1(lines[1]) != norad_id:
            logger.warning("NORAD mismatch: asked %d, got %r", norad_id, lines[1][:7])
            return None
        return lines[0].strip(), lines[1], lines[2]

    logger.warning("No valid TLE in response for NORAD %d", norad_id)
    return None


def fetch_n2yo_tle(
    norad_id: int,
    api_key: str,
    on_usage: Callable[[int], None] | None = None,
) -> tuple[str, str, str] | None:
    """Fetch and validate one TLE using the N2YO REST endpoint.

    on_usage, if given, is called with info.transactionscount whenever the
    response carries it (including error / no-data responses).

    Returns (name, l1, l2), or None for an unparseable body (transient).
    Raises UnexpectedStatus, NetworkError, NoData, ProviderRejected or
    RateLimited.
    """
    safe_api_key = urllib.parse.quote(api_key, safe="")
    url = N2YO_TLE_URL.format(norad=norad_id, api_key=safe_api_key)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise UnexpectedStatus(exc.code) from exc
    except Exception as exc:
        # Never include the request URL or API key in logs.
        raise NetworkError(f"N2YO request failed ({type(exc).__name__})") from exc

    if status != 200:
        raise UnexpectedStatus(status)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("N2YO returned invalid JSON for NORAD %d", norad_id)
        return None
    if not isinstance(payload, dict):
        logger.warning("N2YO returned an unexpected payload for NORAD %d", norad_id)
        return None

    info = payload.get("info")

    # Report usage before any error handling so the caller always sees it.
    if on_usage is not None and isinstance(info, dict):
        count = info.get("transactionscount")
        if isinstance(count, int) and not isinstance(count, bool):
            on_usage(count)

    # N2YO can report errors inside a 200 response.  The substring checks
    # below are a best guess - verify them against real responses.
    if payload.get("error"):
        text = str(payload["error"])
        lowered = text.lower()
        logger.warning("N2YO error for NORAD %d: %s", norad_id, text)
        if any(w in lowered for w in ("exceed", "limit", "too many", "quota")):
            raise RateLimited(text)
        if "key" in lowered:
            raise ProviderRejected(text)
        raise NoData(norad_id)
    if "info" in payload and "tle" not in payload:
        raise NoData(norad_id)

    if not isinstance(payload.get("tle"), str):
        logger.warning("N2YO returned no TLE for NORAD %d", norad_id)
        return None

    lines = [line.strip() for line in payload["tle"].splitlines() if line.strip()]
    line1 = next((ln for ln in lines if ln.startswith("1 ")), None)
    line2 = next((ln for ln in lines if ln.startswith("2 ")), None)
    if line1 is None or line2 is None:
        logger.warning("N2YO returned an incomplete TLE for NORAD %d", norad_id)
        return None
    if norad_id_from_line1(line1) != norad_id:
        logger.warning("N2YO returned a mismatched TLE for NORAD %d", norad_id)
        return None

    name = info.get("satname") if isinstance(info, dict) else None
    return (str(name).strip() if name else f"NORAD {norad_id}", line1, line2)


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


def _load_runtime_state() -> tuple[list[float], float]:
    """Return (request_log, n2yo_paused_until) from the runtime cache file.

    Older files simply lack these keys.
    """
    try:
        data = json.loads(TLE_CACHE_PATH.read_text())
        if not isinstance(data, dict):
            return [], 0.0
        log = [float(t) for t in data.get("request_log", [])]
        paused = float(data.get("n2yo_paused_until", 0.0))
        return log, paused
    except Exception:
        return [], 0.0


def save_cache(
    entries: dict[int, CacheEntry],
    retry: dict[int, RetryState],
    blocked_until: float = 0.0,
    request_log: list[float] | None = None,
    n2yo_paused_until: float = 0.0,
) -> None:
    """Persist TLEs, retry state, request block and usage state atomically."""
    payload = {
        "version": CACHE_VERSION,
        "blocked_until": blocked_until,
        "n2yo_paused_until": n2yo_paused_until,
        "request_log": request_log or [],
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

    Retry, usage and request-block state stay in the device-local runtime cache.
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
# Config
# ---------------------------------------------------------------------------


def _resolve_source() -> tuple[str, str]:
    """Return (source, n2yo_api_key) from config, with safe defaults."""
    cfg = Config.instance()
    source = (getattr(cfg, "satellite_tle_source", None) or "celestrak").strip().lower()
    if source not in ("celestrak", "n2yo"):
        logger.error("Unknown satellite_tle_source %r - using celestrak", source)
        source = "celestrak"
    api_key = (getattr(cfg, "n2yo_api_key", None) or "").strip()
    return source, api_key


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
        self.request_log: list[float] = []
        self.net_retry_at = 0.0
        self.net_backoff = 0.0
        self.n2yo_paused_until = 0.0
        self.active_source: str | None = None
        self.config_hold = False
        self.rejected_key: str | None = None  # N2YO key the server refused
        # Debug: when True, the cache is served as-is and no provider is
        # contacted.  Defaults to the TLE_CACHE_ONLY environment variable.
        if cache_only is None:
            cache_only = os.environ.get(CACHE_ONLY_ENV, "").lower() in ("1")
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

        Newly added satellites are fetched; satellites that are cached and
        still fresh are NOT re-requested.  Retry delays are kept, so a config
        edit cannot cause a retry storm.  Pass force=True to also clear retry
        delays and refetch everything older than MIN_REFETCH_INTERVAL.
        Existing cached TLEs are kept as a fallback, so a failed forced
        refresh loses nothing.

        This never lifts a policy block, the N2YO usage pause, or the daily
        budget.  (Changing the N2YO API key does lift a key-rejected hold.)
        """
        with self.lock:
            if force:
                self.force = True
                self.retry.clear()
        self.ready.clear()
        self.wake.set()

    def set_cache_only(self, enabled: bool) -> None:
        """Debug: toggle cache-only mode at runtime.

        While enabled, no request is ever sent; whatever is on disk / in
        memory is served, however old.  Disabling resumes normal refreshing
        (retry delays and any provider request block still apply).
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
        """IDs that are missing/expired and not in a retry delay.

        Missing entries come first, then the oldest, so scarce budget is spent
        where it matters most.  Caller must hold self.lock.
        """
        due = []
        for nid in ids:
            entry = self.entries.get(nid)
            if entry is None:
                expired = True
            else:
                age = now - entry.fetched_at
                expired = age >= TLE_CACHE_TTL or (
                    force and age >= MIN_REFETCH_INTERVAL
                )
            state = self.retry.get(nid)
            if expired and (state is None or now >= state.retry_at):
                due.append(nid)
        due.sort(
            key=lambda n: (
                n in self.entries,
                self.entries[n].fetched_at if n in self.entries else 0.0,
            )
        )
        return due

    def budget_remaining(self, now: float) -> int:
        """Caller must hold self.lock."""
        cutoff = now - BUDGET_WINDOW
        self.request_log = [t for t in self.request_log if t > cutoff]
        return max(0, DAILY_REQUEST_BUDGET - len(self.request_log))

    def _snapshot_locked(self):
        """State to persist.  Caller must hold self.lock."""
        return (
            dict(self.entries),
            dict(self.retry),
            self.blocked_until,
            list(self.request_log),
            self.n2yo_paused_until,
        )

    def _save_state(self) -> None:
        with self.lock:
            snap = self._snapshot_locked()
        save_cache(*snap)

    def _rearm_force(self) -> None:
        with self.lock:
            self.force = True

    def seconds_until_next_check(self) -> float:
        if self.cache_only or self.config_hold:
            return MAX_SLEEP
        ids = self.configured_ids()
        now = time.time()
        with self.lock:
            floor = max(self.blocked_until, self.net_retry_at)
            if self.active_source == "n2yo":
                floor = max(floor, self.n2yo_paused_until)
            if self.budget_remaining(now) <= 0 and self.request_log:
                floor = max(floor, min(self.request_log) + BUDGET_WINDOW)
            waits = []
            for nid in ids:
                entry = self.entries.get(nid)
                expiry = entry.fetched_at + TLE_CACHE_TTL if entry else 0.0
                state = self.retry.get(nid)
                retry_at = state.retry_at if state else 0.0
                waits.append(max(expiry, retry_at, floor) - now)
        if not waits:
            return MAX_SLEEP
        return max(1.0, min(min(waits), MAX_SLEEP))

    # -- refresh loop -------------------------------------------------------

    def run_loop(self) -> None:
        self.prime_from_disk()

        while True:
            self.wake.clear()  # before refresh, so an invalidate() isn't lost
            try:
                self.refresh()
                timeout = self.seconds_until_next_check()
            except Exception:
                logger.exception("TLE refresh loop error")
                self.ready.set()
                timeout = MAX_SLEEP
            self.wake.wait(timeout=timeout)

    def prime_from_disk(self) -> None:
        """Load the disk cache, retry state, usage state and block at startup.

        Expired entries are still served (up to STALE_SERVE_MAX) so pass
        prediction keeps working during a provider outage; refresh() will
        try to replace them right after, honouring any persisted retry delay
        and any active request block.
        """
        now = time.time()

        # Usage state first: it must survive even if no TLEs are cached.
        log, paused_until = _load_runtime_state()
        log = [t for t in log if now - BUDGET_WINDOW < t <= now]
        paused_until = min(paused_until, now + N2YO_PAUSE)
        if paused_until <= now:
            paused_until = 0.0
        with self.lock:
            self.request_log = log
            self.n2yo_paused_until = paused_until
        if paused_until:
            logger.warning(
                "N2YO requests are paused for another %.0f min (usage limit)",
                (paused_until - now) / 60.0,
            )

        cached, retry, blocked_until = load_cache()
        if not cached and not retry and not blocked_until:
            return

        usable: dict[int, CacheEntry] = {}
        for nid, entry in cached.items():
            # A clock that was ahead at fetch time would otherwise make an
            # entry look fresh for too long.
            fetched_at = min(entry.fetched_at, now)
            age = now - fetched_at
            entry = CacheEntry(fetched_at, entry.tle)
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
                "TLE requests are suspended for another %.1f hours "
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

        # Resolve the provider ONCE per refresh, before any state is touched,
        # so a batch can never mix sources and a config problem consumes nothing.
        source, api_key = _resolve_source()
        was_hold = self.config_hold
        self.config_hold = False
        if source == "n2yo":
            if self.rejected_key is not None and api_key != self.rejected_key:
                logger.info("N2YO API key changed - clearing rejected-key hold")
                self.rejected_key = None
            hold_reason = None
            if not api_key:
                hold_reason = "N2YO selected but no API key configured"
            elif api_key == self.rejected_key:
                hold_reason = "N2YO rejected the configured API key (change it to retry)"
            if hold_reason:
                (logger.debug if was_hold else logger.error)(
                    "%s; serving cached TLEs", hold_reason
                )
                self.config_hold = True
                self.ready.set()
                return

        # Highest N2YO usage counter seen during this batch.
        tx_count: list[int | None] = [None]

        def record_usage(n: int) -> None:
            tx_count[0] = n

        if source == "n2yo":

            def fetch(n: int):
                return fetch_n2yo_tle(n, api_key, record_usage)

        else:
            fetch = fetch_tle

        # 1. Gating: block, cooldown, N2YO pause, due list, budget.
        with self.lock:
            now = time.time()

            # Provider switched: drop soft state that belonged to the old one.
            # (blocked_until stays shared as the safe default.)
            if source != self.active_source:
                if self.active_source is not None:
                    logger.info(
                        "TLE source changed %s -> %s; resetting retry state",
                        self.active_source,
                        source,
                    )
                    self.retry.clear()
                self.net_retry_at = 0.0
                self.net_backoff = 0.0
                self.active_source = source

            blocked_for = self.blocked_until - now
            cooling_for = self.net_retry_at - now
            paused_for = self.n2yo_paused_until - now if source == "n2yo" else 0.0
            due: list[int] = []
            budget = 0
            force = False
            if blocked_for <= 0 and cooling_for <= 0 and paused_for <= 0:
                force, self.force = self.force, False
                due = self.due_ids(ids, now, force)
                budget = self.budget_remaining(now)

        if blocked_for > 0:
            logger.debug(
                "TLE requests suspended for another %.1f hours", blocked_for / 3600.0
            )
            self.ready.set()
            return
        if cooling_for > 0:
            logger.debug("Network cooldown: %.0fs left", cooling_for)
            self.ready.set()
            return
        if paused_for > 0:
            logger.debug("N2YO usage pause: %.0f min left", paused_for / 60.0)
            self.ready.set()
            return
        if not due:
            logger.debug("No TLE fetch needed for %d satellite(s)", len(ids))
            self.ready.set()
            return
        if budget <= 0:
            logger.warning(
                "Daily TLE request budget (%d) exhausted", DAILY_REQUEST_BUDGET
            )
            if force:
                self._rearm_force()  # don't lose a forced refresh to the budget
            self.ready.set()
            return
        truncated = len(due) > budget
        if truncated:
            logger.warning(
                "Only %d/%d due satellites fit in today's budget", budget, len(due)
            )
            due = due[:budget]

        # 2. Fetch only what's due (outside the lock - network I/O).
        logger.debug(
            "TLE refresh starting for %d/%d satellite(s) via %s",
            len(due),
            len(ids),
            source,
        )

        fetched: dict[int, tuple[str, str, str]] = {}
        failed: list[int] = []
        not_found: list[int] = []
        bad_status: int | None = None
        net_failed = False
        rejected = False
        rate_limited = False
        attempted = 0
        for nid in due:
            if self.cache_only:
                logger.warning("Cache-only mode enabled mid-refresh - aborting fetches")
                break
            if tx_count[0] is not None and tx_count[0] >= N2YO_TX_STOP:
                break  # N2YO usage limit reached; handled after the loop

            with self.lock:
                self.request_log.append(time.time())  # attempts, not successes
            attempted += 1
            if attempted % REQUEST_LOG_SAVE_EVERY == 0:
                self._save_state()  # a crash mid-batch can't hide attempts

            try:
                tle = fetch(nid)
            except UnexpectedStatus as exc:
                bad_status = exc.status
                logger.critical(
                    "TLE provider (%s) returned HTTP %d for NORAD %d "
                    "(expected 200) - suspending all TLE requests for %d hours",
                    source,
                    exc.status,
                    nid,
                    int(BLOCK_DURATION // 3600),
                )
                break
            except RateLimited:
                rate_limited = True
                break
            except ProviderRejected as exc:
                rejected = True
                logger.critical(
                    "TLE provider (%s) rejected the API key (%s) - "
                    "holding requests until the key is changed",
                    source,
                    exc,
                )
                break
            except NetworkError as exc:
                net_failed = True
                logger.error(
                    "Network error fetching NORAD %d: %s - aborting batch", nid, exc
                )
                break  # the rest would fail identically
            except NoData:
                not_found.append(nid)
                continue

            if tle:
                fetched[nid] = tle
            else:
                failed.append(nid)

        limit_hit = source == "n2yo" and (
            rate_limited or (tx_count[0] is not None and tx_count[0] >= N2YO_TX_STOP)
        )
        if limit_hit:
            logger.warning(
                "N2YO usage limit reached (transactionscount=%s, stop at %d) - "
                "pausing N2YO requests for %.0f min",
                tx_count[0] if tx_count[0] is not None else "rate-limited",
                N2YO_TX_STOP,
                N2YO_PAUSE / 60.0,
            )
        elif source == "n2yo" and tx_count[0] is not None:
            logger.debug("N2YO transactionscount=%d", tx_count[0])

        # A forced refresh that was cut short must not be forgotten.
        rearm_force = force and (truncated or net_failed or limit_hit)

        # 3. Merge: update only retrieved entries, record retry state for the
        #    rest, then persist everything.
        now = time.time()
        with self.lock:
            # --- successes ---------------------------------------------------
            for nid in list(fetched):
                tle = fetched[nid]
                prev = self.entries.get(nid)
                if source == "n2yo" and prev is not None:
                    # Keep the existing satellite name so it doesn't flip
                    # between providers' naming styles.
                    tle = (prev.tle[0], tle[1], tle[2])
                    fetched[nid] = tle
                self.entries[nid] = CacheEntry(now, tle)
                self.retry.pop(nid, None)

            # --- provider has no data for this ID (HTTP 200) ------------------
            # Not transient: recheck at most once a day.
            for nid in not_found:
                self.retry[nid] = RetryState(now + NOT_FOUND_RETRY, 0.0)
                logger.warning(
                    "TLE provider (%s) has no data for NORAD %d - "
                    "rechecking in %d h (is the ID correct / has it decayed?)",
                    source,
                    nid,
                    int(NOT_FOUND_RETRY // 3600),
                )

            # --- unparseable body / NORAD mismatch (possibly transient) ------
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
                    prev_backoff = self.retry.get(nid, RetryState(0.0, 0.0)).backoff
                    delay = (
                        BACKOFF_MIN
                        if prev_backoff <= 0.0
                        else min(prev_backoff * 2.0, BACKOFF_MAX)
                    )
                    self.retry[nid] = RetryState(now + delay, delay)
                    logger.warning(
                        "TLE refresh failed for NORAD %d - no cached data; "
                        "next retry in %.0fs",
                        nid,
                        delay,
                    )

            # --- global network cooldown (no HTTP response at all) -----------
            if net_failed:
                self.net_backoff = (
                    NET_BACKOFF_MIN
                    if self.net_backoff <= 0.0
                    else min(self.net_backoff * 2.0, NET_BACKOFF_MAX)
                )
                self.net_retry_at = now + self.net_backoff
                logger.warning(
                    "Network failure - pausing all TLE requests for %.0fs",
                    self.net_backoff,
                )
            else:
                # Any batch that got an HTTP response proves the network works.
                self.net_backoff = 0.0
                self.net_retry_at = 0.0

            # --- N2YO usage pause / rejected key -----------------------------
            if limit_hit:
                self.n2yo_paused_until = now + N2YO_PAUSE
            if rejected:
                self.rejected_key = api_key

            # --- policy block (non-200) --------------------------------------
            # Start a new block, or clear an expired one.
            self.blocked_until = now + BLOCK_DURATION if bad_status is not None else 0.0

            if rearm_force:
                self.force = True

            # --- housekeeping ------------------------------------------------
            # Drop entries too old to ever serve...
            for nid in [
                n
                for n, e in self.entries.items()
                if now - e.fetched_at >= STALE_SERVE_MAX
            ]:
                del self.entries[nid]

            # ...and retry state for satellites that are no longer configured.
            wanted = set(ids)
            for nid in [n for n in self.retry if n not in wanted]:
                del self.retry[nid]

            # Prune request log to the budget window before persisting.
            cutoff = now - BUDGET_WINDOW
            self.request_log = [t for t in self.request_log if t > cutoff]

            snap = self._snapshot_locked()

        save_cache(*snap)
        _update_backup_cache(fetched, now)

        if bad_status is not None:
            logger.info(
                "TLE refresh aborted after HTTP %d - %d/%d due satellite(s) "
                "updated before the block",
                bad_status,
                len(fetched),
                len(due),
            )
        elif rejected:
            logger.info(
                "TLE refresh aborted: API key rejected - %d/%d due satellite(s) "
                "updated",
                len(fetched),
                len(due),
            )
        elif limit_hit:
            logger.info(
                "TLE refresh stopped at N2YO usage limit - %d/%d due satellite(s) "
                "updated",
                len(fetched),
                len(due),
            )
        elif net_failed:
            logger.info(
                "TLE refresh aborted after network error - %d/%d due satellite(s) "
                "updated",
                len(fetched),
                len(due),
            )
        else:
            logger.info(
                "TLE refresh complete - %d/%d due satellite(s) updated "
                "(%d not found, %d failed)",
                len(fetched),
                len(due),
                len(not_found),
                len(failed),
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
            "TLE request block active for another %.1f hours",
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