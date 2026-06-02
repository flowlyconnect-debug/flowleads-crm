"""Search profile UI choices (remonttityypit, maakunnat, schedules)."""

from __future__ import annotations

REMONTTITYYPIT: tuple[str, ...] = (
    "Lukkoremontti / kulunvalvonta",
    "Putkiremontti",
    "Sähkö- ja tietoliikennejärjestelmät",
    "Lämmitysjärjestelmän remontti",
    "Julkisivuremontti",
    "Katto- ja vesikattoremontti",
    "Huoneistoremontit",
    "Muu remontti",
)

FINNISH_REGIONS: tuple[str, ...] = (
    "Uusimaa",
    "Varsinais-Suomi",
    "Satakunta",
    "Kanta-Häme",
    "Pirkanmaa",
    "Päijät-Häme",
    "Kymenlaakso",
    "Etelä-Karjala",
    "Etelä-Savo",
    "Pohjois-Savo",
    "Pohjois-Karjala",
    "Keski-Suomi",
    "Etelä-Pohjanmaa",
    "Pohjanmaa",
    "Keski-Pohjanmaa",
    "Pohjois-Pohjanmaa",
    "Lappi",
)

SEARCH_SCHEDULE_LABELS: dict[str, str] = {
    "daily": "Päivittäin",
    "weekly": "Viikoittain",
    "manual": "Manuaalinen",
}

SEARCH_JOB_STATUS_LABELS: dict[str, str] = {
    "pending": "Odottaa",
    "running": "Käynnissä",
    "completed": "Valmis",
    "failed": "Epäonnistui",
}
