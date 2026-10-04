"""Route lookup via the official FlightRadar24 API.

The live-position /full variant carries the flight's origin/destination
airports and the marketing airline, so a callsign-filtered positions
query resolves routing for any airborne flight.  Unlike the free FR24
feed no live position is needed - the API filters by callsign directly.
"""

from __future__ import annotations

import logging

from utilities.lookups.providers.common.airports import (
    fill_airport_details,
    icao_to_iata_code,
)
from utilities.lookups.providers.fr24api.client import (
    api_key,
    api_unavailable,
    callsign_position,
    data_of,
    is_transport_error,
)
from utilities.lookups.results import LookupResult, RouteInfo

logger = logging.getLogger(__name__)


class RouteProvider:
    """FR24 API route capability (requires a paid token)."""

    def __init__(self, settings: dict | None = None):
        self._settings = settings or {}
        self._api_key = api_key(self._settings)

    def lookup_route(self, ctx) -> LookupResult:
        callsign = (ctx.callsign or "").strip()
        if not callsign:
            return LookupResult.not_found("no callsign")
        if not self._api_key:
            return LookupResult.unavailable("FR24 API token not configured")

        try:
            # Shared with the aircraft capability: both capabilities need
            # the identical callsign request, and every call is billed.
            resp = callsign_position(self._api_key, callsign)
        except Exception as e:
            if is_transport_error(e):
                logger.debug("FR24 API route lookup failed for %r: %s", callsign, e)
                return LookupResult.unavailable(f"FR24 API unreachable: {e}")
            raise

        if resp.status_code in (400, 404):
            # Ident rejected or unknown: "no answer for this callsign",
            # not a provider failure - must not quarantine.  (#101 family)
            logger.debug(
                "FR24 API rejected ident %r (HTTP %d)",
                callsign,
                resp.status_code,
            )
            return LookupResult.not_found("FR24 API rejected the ident format")

        if resp.status_code != 200:
            reason = api_unavailable(resp)
            logger.debug("FR24 API routes for %r: %s", callsign, reason)
            return LookupResult.unavailable(reason)

        data = data_of(resp)
        if not data:
            return LookupResult.not_found("no FR24 API flight for this callsign")

        record = data[0]
        origin_icao = (record.get("orig_icao") or "").strip().upper()
        destination_icao = (record.get("dest_icao") or "").strip().upper()
        
        if not origin_icao and not destination_icao:
            return LookupResult.not_found("FR24 API flight has no route codes")

        route = RouteInfo()
        route.origin = origin_icao
        route.destination = destination_icao
        route.origin_icao = origin_icao
        route.destination_icao = destination_icao
        
        fill_airport_details(route, "origin", icao_code=origin_icao)
        fill_airport_details(route, "destination", icao_code=destination_icao)
        route.airline_icao = (record.get("painted_as") or "").strip().upper()

        logger.debug(
            "FR24 API route for %r: %s->%s", callsign, route.origin, route.destination
        )
        return LookupResult.found(route)


def _airport_code(record: dict, side: str) -> str:
    """Return the IATA equivalent of ICAO when available, otherwise the feed code."""
    code = (record.get(f"{side}_iata") or "").strip().upper()
    icao = (record.get(f"{side}_icao") or "").strip().upper()
    if icao:
        return icao_to_iata_code(icao) or code or icao  # ← Converting to IATA!
    return code
