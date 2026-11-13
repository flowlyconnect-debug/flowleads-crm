"""The renovation timeline ("PTS strip") shown on Kohteet, the dashboard and a
lead: the coming years, each with a short mark for every renovation that
starts that year. Derived from ListingRenovation.timing_year; a renovation
without a year is listed separately as "aikataulu auki"."""

from __future__ import annotations

from datetime import datetime, timezone

PTS_SPAN = 5

# Two-letter mark, short name and colour family (a design-system.css token pair)
# for every type in app/search/constants.py::REMONTTITYYPIT.
RENOVATION_MARKS: dict[str, tuple[str, str, str]] = {
    "Lukkoremontti / kulunvalvonta": ("Lu", "Lukot", "neutral"),
    "Putkiremontti": ("Pu", "Putket", "info"),
    "Sähkö- ja tietoliikennejärjestelmät": ("Sä", "Sähkö", "success"),
    "Lämmitysjärjestelmän remontti": ("Lä", "Lämmitys", "danger"),
    "Julkisivuremontti": ("Ju", "Julkisivu", "warning"),
    "Katto- ja vesikattoremontti": ("Ka", "Katto", "accent"),
    "Huoneistoremontit": ("Hu", "Huoneistot", "neutral"),
    "Muu remontti": ("Mu", "Muu", "neutral"),
}


def renovation_mark(remonttityyppi: str) -> dict:
    abbr, short, tone = RENOVATION_MARKS.get(remonttityyppi, (remonttityyppi[:2], remonttityyppi, "neutral"))
    return {"abbr": abbr, "short": short, "tone": tone, "name": remonttityyppi}


def pts_strip(renovations, *, now: datetime | None = None, span: int = PTS_SPAN) -> dict:
    """{"years": [{"year", "marks": [...]}, ...], "open": [...], "start_year": int|None}.
    A year before this one is shown in this year's cell (it is due now)."""
    this_year = (now or datetime.now(timezone.utc)).year
    years = [{"year": this_year + i, "marks": []} for i in range(span)]
    open_marks = []
    start_year = None
    seen = set()
    for renovation in renovations:
        if renovation is None:
            continue
        mark = renovation_mark(renovation.remonttityyppi)
        year = getattr(renovation, "timing_year", None)
        if not year:
            if mark["abbr"] not in {m["abbr"] for m in open_marks}:
                open_marks.append(mark)
            continue
        year = max(int(year), this_year)
        start_year = year if start_year is None else min(start_year, year)
        key = (mark["abbr"], year)
        if key in seen:
            continue
        seen.add(key)
        if year < this_year + span:
            years[year - this_year]["marks"].append(dict(mark, year=year))
        else:
            open_marks.append(dict(mark, year=year))
    return {"years": years, "open": open_marks, "start_year": start_year}


def pts_from_text(remonttityyppi: str | None, timing: str | None, *, now: datetime | None = None) -> dict:
    """The strip for a lead or call that only stores the renovation as text
    (Lead.ai_contact_info: remonttityyppi, remontin_ajankohta)."""
    import re
    from types import SimpleNamespace

    if not remonttityyppi:
        return pts_strip([], now=now)
    match = re.search(r"(20\d\d)", str(timing or ""))
    return pts_strip([SimpleNamespace(remonttityyppi=remonttityyppi, timing_year=int(match.group(1)) if match else None)], now=now)
