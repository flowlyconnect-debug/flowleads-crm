"""HTTP clients for the open registers. Both are open data (CC BY 4.0) with no
key: Ryhti OGC API Features (SYKE) and the PRH YTJ v3 API.

`fetch` is injectable so tests never touch the network."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from datetime import date

import requests
from flask import current_app

logger = logging.getLogger(__name__)

Fetch = Callable[[str, dict], dict]

RYHTI_BUILDINGS = "ryhti_building/ogc/features/v1/collections/avoimet_rakennukset/items"
RYHTI_PERMITS = "ryhti_building/ogc/features/v1/collections/avoimet_lupa_rakennukset/items"
RYHTI_ADDRESSES = "ryhti_building/ogc/features/v1/collections/open_address/items"
PRH_COMPANIES = "opendata-ytj-api/v3/companies"

# Ryhti open classification: "Kerrostalo", and "Pientalo" covers detached,
# semi-detached and terraced houses, so 3+ apartments is the rivitalo proxy.
_RESIDENTIAL = (
    "(paaasiallinen_kayttotarkoitus='Kerrostalo'"
    " OR (paaasiallinen_kayttotarkoitus='Pientalo' AND huoneistojen_lukumaara>=3))"
)
# Permit actions that change an existing building (not new builds or extensions)
ALTERATION_ACTIONS = ("Muu muutostyö", "Uudelleen rakentamiseen verrattava muutostyö")
PRH_HOUSING_COMPANY_FORM = "AOY"
PRH_PAGE_SIZE = 100


class RegistryFetchError(Exception):
    pass


def _cfg(name: str, default):
    try:
        return current_app.config.get(name, default)
    except RuntimeError:
        return default


def default_fetch(url: str, params: dict) -> dict:
    """GET JSON with a few retries on network errors, 429 and 5xx."""
    timeout = int(_cfg("REGISTRY_HTTP_TIMEOUT", 60))
    headers = {"User-Agent": "FlowLeads-registry-sync/1.0", "Accept": "application/json"}
    last_error = None
    for attempt in range(4):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = str(exc)
        else:
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}"
            if response.status_code not in (429, 500, 502, 503, 504):
                break
        time.sleep(2**attempt)
    raise RegistryFetchError(f"{url}: {last_error}")


def _quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class RyhtiClient:
    def __init__(self, fetch: Fetch | None = None, *, base: str | None = None, page_size: int | None = None):
        self.fetch = fetch or default_fetch
        self.base = (base or _cfg("RYHTI_API_BASE", "https://paikkatiedot.ymparisto.fi/geoserver")).rstrip("/")
        self.page_size = page_size or int(_cfg("REGISTRY_PAGE_SIZE", 2000))

    def _items(self, path: str, cql: str, sortby: str, *, max_pages: int | None = None) -> Iterator[dict]:
        start = 0
        pages = 0
        while True:
            params = {
                "f": "json",
                "limit": self.page_size,
                "startIndex": start,
                "sortby": sortby,
                "filter-lang": "cql2-text",
                "filter": cql,
            }
            data = self.fetch(f"{self.base}/{path}", params)
            features = data.get("features") or []
            for feature in features:
                props = dict(feature.get("properties") or {})
                coords = ((feature.get("geometry") or {}).get("coordinates")) or None
                if coords and len(coords) >= 2:
                    props["_lon"], props["_lat"] = coords[0], coords[1]
                yield props
            pages += 1
            if len(features) < self.page_size or (max_pages and pages >= max_pages):
                return
            start += len(features)

    def buildings(self, *, since: str | None = None, municipality: str | None = None, max_pages: int | None = None):
        cql = f"{_RESIDENTIAL} AND elinkaaren_tila='Käytössä'"
        if municipality:
            cql += f" AND kunta={_quote(municipality)}"
        if since:
            cql += f" AND aikaleima_utc>{_quote(since)}"
        return self._items(RYHTI_BUILDINGS, cql, "aikaleima_utc,rakennusavain", max_pages=max_pages)

    def permits(self, *, decided_since: date, since: str | None = None, max_pages: int | None = None):
        actions = ",".join(_quote(a) for a in ALTERATION_ACTIONS)
        cql = (
            f"{_RESIDENTIAL} AND toimenpiteen_laji IN ({actions})"
            f" AND paatospaivamaara>={_quote(decided_since.isoformat())}"
        )
        if since:
            cql += f" AND aikaleima_utc>{_quote(since)}"
        return self._items(RYHTI_PERMITS, cql, "aikaleima_utc,rakennusavain", max_pages=max_pages)

    def addresses(self, building_keys: list[str]) -> list[dict]:
        if not building_keys:
            return []
        keys = ",".join(_quote(k) for k in building_keys)
        return list(self._items(RYHTI_ADDRESSES, f"building_key IN ({keys})", "address_key"))


class PrhClient:
    def __init__(self, fetch: Fetch | None = None, *, base: str | None = None):
        self.fetch = fetch or default_fetch
        self.base = (base or _cfg("PRH_API_BASE", "https://avoindata.prh.fi")).rstrip("/")

    def housing_companies(self, location: str, *, max_pages: int | None = None) -> Iterator[dict]:
        """Every registered asunto-osakeyhtiö whose address is in `location`."""
        page = 1
        while True:
            data = self.fetch(
                f"{self.base}/{PRH_COMPANIES}",
                {"companyForm": PRH_HOUSING_COMPANY_FORM, "location": location, "page": page},
            )
            companies = data.get("companies") or []
            yield from companies
            total = int(data.get("totalResults") or 0)
            if not companies or page * PRH_PAGE_SIZE >= total or (max_pages and page >= max_pages):
                return
            page += 1
