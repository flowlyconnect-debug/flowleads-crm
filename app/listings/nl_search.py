"""Kohteet search written in plain Finnish: "putkiremontit Tampereella 2027
mennessä kerrostaloissa, joilla on yhteystiedot".

The text becomes the same filters the filter bar sets (renovation, region,
city, year, building, contact, sort) and nothing else: it never becomes SQL.
The rule-based parser below always works; with NL_SEARCH_AI_ENABLED and an
OpenAI key the model may be asked first, and its answer is accepted only for
values the filter bar offers.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from app.listings.housing import fold
from app.search.constants import FINNISH_REGIONS

logger = logging.getLogger(__name__)
MAX_QUERY_LENGTH = 300

# renovation type -> folded word stems that mean it
RENOVATION_WORDS: dict[str, tuple[str, ...]] = {
    "Putkiremontti": ("putki", "putke", "linjasaneera", "linjasaneeraus", "viemari", "vesijohto", "sukitu", "putkisto"),
    "Julkisivuremontti": ("julkisivu", "ikkuna", "parveke", "parvekk", "rappau", "saumau"),
    "Katto- ja vesikattoremontti": ("katto", "katot", "katon", "vesikatto", "ylapohja", "kattoremont"),
    "Lukkoremontti / kulunvalvonta": ("lukko", "lukot", "lukkoj", "lukitu", "kulunvalvon", "iloq", "sarjoitu", "avainjarjest"),
    "Sähkö- ja tietoliikennejärjestelmät": ("sahkoremont", "sahkojarjest", "sahkoverk", "sahkosaneera", "sahkot", "valokuitu", "tietoliikent", "antenni", "nousujohto"),
    "Lämmitysjärjestelmän remontti": ("lammity", "maalampo", "kaukolampo", "lammonjako", "patteri", "lampopumpp"),
    "Huoneistoremontit": ("huoneistoremont", "kylpyhuone", "keittio", "pintaremont"),
    "Muu remontti": ("muu remontti", "muut remontit"),
}
BUILDING_WORDS = {
    "Kerrostalo": ("kerrostal",),
    "Rivitalo": ("rivital",),
    "Paritalo": ("parital",),
    "Omakotitalo": ("omakotital", "okt"),
}
CONTACT_WORDS = ("yhteystied", "puhelinnum", "puhelin", "sahkopost", "numero")
SORT_WORDS = {
    "": ("parhaat", "parhaimmat", "kuumimmat", "lupaavimmat"),
    "newest": ("uusimmat", "tuoreimmat", "uudet", "uusia", "uudet"),
    "oldest": ("vanhimmat",),
    "confidence": ("varmimmat", "varmat"),
}
# region names whose inflected forms do not start with the base form
REGION_FORMS = {
    "Uusimaa": ("uusimaa", "uudellamaalla", "uudeltamaalta", "uudenmaan", "uudellemaalle"),
    "Lappi": ("lappi", "lapissa", "lapin", "lappiin", "lapista"),
    "Kainuu": ("kainuu", "kainuussa", "kainuun"),
}
STOPWORDS = {
    "ja", "tai", "seka", "etta", "joilla", "joissa", "jossa", "jolla", "joiden", "jotka", "on", "ovat", "olevat",
    "kohteet", "kohteita", "kohde", "kohteet", "taloyhtiot", "taloyhtioita", "taloyhtio", "taloyhtiot",
    "remontit", "remontti", "remonttia", "remontteja", "remontin", "haluan", "nayta", "etsi", "hae", "kaikki",
    "alueella", "alueelta", "alue", "kaupungissa", "kunnassa", "tulossa", "tuleva", "tulevat", "tulevia",
    "suunnitteilla", "kanssa", "mennessa", "viimeistaan", "ennen", "vuonna", "vuoden", "vuoteen", "aikana",
    "tana", "ensi", "pian", "lahivuosina", "lahiaikoina", "taloissa", "talot", "taloja", "talo", "missa",
    "ne", "niita", "jo", "vain", "pelkat", "pelkastaan", "myos", "seuraavat", "listaa", "minulle",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _key(text: str) -> str:
    """Folded like housing.fold(), but hyphens kept ("pohjois-pohjanmaa")."""
    return fold((text or "").replace("-", " zzhyphenzz ")).replace(" zzhyphenzz ", "-").replace("zzhyphenzz", "-")


def _tokens(text: str) -> list[str]:
    return _key(text).split()


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def _place_match(token: str, name_key: str) -> bool:
    """'tampereella' ~ 'tampere', 'helsingissa' ~ 'helsinki', 'turussa' ~ 'turku':
    the inflected word starts with the name, or shares all but its last two
    letters (consonant gradation) and is at least as long."""
    if token == name_key or token.startswith(name_key):
        return True
    if len(name_key) < 4 or len(token) < len(name_key):
        return False
    return _common_prefix(token, name_key) >= len(name_key) - 2 and _common_prefix(token, name_key) >= 3


def parse_query(text: str, options: dict, *, now: datetime | None = None) -> dict:
    """{"params": {filter key: value}, "recognized": [Finnish labels],
    "unknown": [words that meant nothing], "search": free text or ""}.
    `options` is target_filter_options(): only values that exist there (and
    the 18 regions) are produced."""
    now = now or _now()
    text = (text or "").strip()[:MAX_QUERY_LENGTH]
    tokens = _tokens(text)
    used: set[int] = set()
    params: dict[str, str] = {}
    recognized: list[str] = []
    notes: list[str] = []

    def mark(index: int) -> None:
        used.add(index)

    # contact words first: "sähköposti" must not read as "sähkö"
    for i, token in enumerate(tokens):
        if any(token.startswith(word) for word in CONTACT_WORDS):
            params["contact"] = "1"
            mark(i)
    if params.get("contact"):
        recognized.append("vain yhteystiedolliset")

    # renovation
    own = list(options.get("renovations") or [])
    folded_text = " ".join(tokens)
    for rtype, words in RENOVATION_WORDS.items():
        hit = None
        for i, token in enumerate(tokens):
            if i in used:
                continue
            if any(token.startswith(word) for word in words if " " not in word):
                hit = i
                break
        if hit is None and any(word in folded_text for word in words if " " in word):
            hit = -1
        if hit is None:
            continue
        if hit >= 0:
            mark(hit)
        if own and rtype not in own:
            notes.append(f"{rtype} ei ole urakoissasi")
            continue
        if "renovation" not in params:
            params["renovation"] = rtype
            recognized.append(rtype)

    # building type
    buildings = set(options.get("buildings") or [])
    for i, token in enumerate(tokens):
        if i in used:
            continue
        for building, words in BUILDING_WORDS.items():
            if any(token.startswith(word) for word in words):
                mark(i)
                if (not buildings or building in buildings) and "building" not in params:
                    params["building"] = building
                    recognized.append(building)

    # region (longest names first: Pohjois-Pohjanmaa before Pohjanmaa)
    regions = sorted(FINNISH_REGIONS, key=len, reverse=True)
    for i, token in enumerate(tokens):
        if i in used or "region" in params:
            continue
        for region in regions:
            forms = REGION_FORMS.get(region, (_key(region),))
            if token in forms or (region not in REGION_FORMS and _place_match(token, _key(region))):
                params["region"] = region
                recognized.append(region)
                mark(i)
                break

    # city (from the targets' own cities)
    cities = sorted(options.get("cities") or [], key=len, reverse=True)
    for i, token in enumerate(tokens):
        if i in used or "city" in params:
            continue
        for city in cities:
            if _place_match(token, _key(city)):
                params["city"] = city
                recognized.append(city)
                mark(i)
                break

    # year: "2027 mennessä", "ennen 2028", "tänä vuonna", "ensi vuonna", "lähivuosina"
    year = None
    for i, token in enumerate(tokens):
        if re.fullmatch(r"20\d\d", token):
            value = int(token)
            if i > 0 and tokens[i - 1] == "ennen":
                value -= 1
            year = value
            mark(i)
            break
    if year is None:
        if "tana vuonna" in folded_text:
            year = now.year
        elif "ensi vuonna" in folded_text:
            year = now.year + 1
        elif any(word in folded_text for word in ("lahivuosina", "lahiaikoina", "pian")):
            year = now.year + 2
    if year is not None:
        params["year"] = str(max(year, now.year))
        recognized.append(f"alkaa viimeistään {params['year']}")

    # sort
    for i, token in enumerate(tokens):
        if i in used:
            continue
        for sort, words in SORT_WORDS.items():
            if token in words:
                mark(i)
                if sort:
                    params["sort"] = sort
                recognized.append(
                    {"": "parhaat ensin", "newest": "uusimmat ensin", "oldest": "vanhimmat ensin", "confidence": "varmimmat ensin"}[sort]
                )
                break

    unknown = [
        token
        for i, token in enumerate(tokens)
        if i not in used and token not in STOPWORDS and not re.fullmatch(r"\d+", token) and len(token) > 1
    ]
    search = ""
    if not params and text:
        # nothing structured: a name or an address ("As Oy Kallio")
        search = " ".join(text.split())[:120]
        unknown = []
    return {"params": params, "recognized": recognized, "unknown": unknown + notes, "search": search}


# --- optional AI interpretation ------------------------------------------------------


def _ai_enabled() -> bool:
    from flask import current_app

    return bool(current_app.config.get("NL_SEARCH_AI_ENABLED") and current_app.config.get("OPENAI_API_KEY"))


def _ai_parse(text: str, options: dict, *, now: datetime, client=None) -> dict | None:
    """Ask the model for the filters. Returns parse_query()-shaped output or
    None. Every value is checked against `options`; anything else is dropped."""
    from flask import current_app

    allowed = {
        "renovation": options.get("renovations") or [],
        "region": list(FINNISH_REGIONS),
        "city": options.get("cities") or [],
        "building": options.get("buildings") or [],
    }
    prompt = (
        "Muunna suomenkielinen kohdehaku suodattimiksi. Vastaa vain JSON-objektina avaimilla "
        '"renovation", "region", "city", "building", "year", "contact", "sort". Käytä vain annettuja arvoja '
        'tai null. year = vuosi, johon mennessä remontti alkaa (numero) tai null. contact = true, jos halutaan '
        'vain kohteet, joilla on yhteystiedot. sort = "" (parhaat), "newest", "oldest", "confidence" tai null.\n'
        f"Tänään on {now.date().isoformat()}.\n"
        f"Sallitut arvot: {json.dumps(allowed, ensure_ascii=False)}\n"
        f"Haku: {text}"
    )
    if client is None:
        from openai import OpenAI

        client = OpenAI(api_key=current_app.config["OPENAI_API_KEY"], timeout=15.0)
    response = client.chat.completions.create(
        model=current_app.config.get("AI_ENRICHMENT_MODEL") or "gpt-4o-mini",
        messages=[{"role": "system", "content": "You respond only with valid JSON objects."}, {"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    data = json.loads(response.choices[0].message.content or "{}")
    if not isinstance(data, dict):
        return None
    params: dict[str, str] = {}
    recognized: list[str] = []
    for key, values in allowed.items():
        value = data.get(key)
        if isinstance(value, str) and value in values:
            params[key] = value
            recognized.append(value)
    try:
        year = int(data.get("year")) if data.get("year") not in (None, "") else None
    except (TypeError, ValueError):
        year = None
    if year and now.year <= year <= now.year + 30:
        params["year"] = str(year)
        recognized.append(f"alkaa viimeistään {year}")
    if data.get("contact") is True:
        params["contact"] = "1"
        recognized.append("vain yhteystiedolliset")
    if data.get("sort") in ("newest", "oldest", "confidence"):
        params["sort"] = data["sort"]
    if not params:
        return None
    return {"params": params, "recognized": recognized, "unknown": [], "search": ""}


def interpret(text: str, options: dict, *, now: datetime | None = None, client=None) -> dict:
    """The model's reading when it is enabled and answers sensibly, the rules
    otherwise. Adds "source": "ai" or "rules"."""
    now = now or _now()
    rules = parse_query(text, options, now=now)
    if _ai_enabled() or client is not None:
        try:
            ai = _ai_parse(text, options, now=now, client=client)
        except Exception as exc:  # the rules always answer
            logger.warning("AI search interpretation failed: %s", exc.__class__.__name__)
            ai = None
        if ai:
            return {**ai, "source": "ai"}
    return {**rules, "source": "rules"}
