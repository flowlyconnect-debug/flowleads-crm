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

# Talotyypit, joihin kohde luokitellaan (Oikotien kortti / AI-analyysi).
BUILDING_TYPES: tuple[str, ...] = (
    "Kerrostalo",
    "Rivitalo",
    "Paritalo",
    "Omakotitalo",
    "Muu",
)

ALL_FINLAND_REGION = "Koko Suomi"

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
    "Kainuu",
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
