"""Oikotie pagination helpers shared with n8n Job Dispatcher logic."""

from __future__ import annotations

import math

OIKOTIE_PAGE_SIZE = 24
DEFAULT_MAX_PAGES = 5


def extract_total_count(payload: dict | None) -> int:
    """Read total result count from common Oikotie / search API response shapes."""
    if not isinstance(payload, dict):
        return 0

    for key in ("total", "totalCount", "count"):
        value = payload.get(key)
        if isinstance(value, int) and value >= 0:
            return value

    for nested_key in ("data", "meta"):
        nested = payload.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in ("total", "totalCount", "count"):
            value = nested.get(key)
            if isinstance(value, int) and value >= 0:
                return value
    return 0


def build_page_items(
    total: int,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    page_size: int = OIKOTIE_PAGE_SIZE,
) -> list[dict]:
    """Build page descriptors for Oikotie offset/limit pagination."""
    if max_pages < 1:
        max_pages = DEFAULT_MAX_PAGES
    if page_size < 1:
        page_size = OIKOTIE_PAGE_SIZE

    if total <= 0:
        return [{"page": 1, "offset": 0, "limit": page_size}]

    total_pages = min(max_pages, max(1, math.ceil(total / page_size)))
    return [
        {
            "page": page,
            "offset": (page - 1) * page_size,
            "limit": page_size,
        }
        for page in range(1, total_pages + 1)
    ]
