"""FlowLeadsin oma myyntiprospektointi: korjausurakoitsijat, joille FlowLeadsia voi tarjota.

Logiikka:
1. PRH:n avoin YTJ-rajapinta (avoindata.prh.fi) antaa yritykset toimialoittain
   (LVI, sähkö, katto, maalaus, eristys, rakentaminen ...). Mukaan vain toimivat
   oikeushenkilöt (Oy, Ky, osuuskunta), ei konkurssi/selvitystiloja eikä toiminimiä
   (toiminimi = luonnollinen henkilö, sähköinen suoramarkkinointi vaatisi luvan).
2. Verkkosivu: PRH:n www-kenttä tai nimestä johdetut domain-arvaukset (.fi/.com),
   jotka tarkistetaan DNS:stä (DoH) ja varmistetaan sivun sisällöstä (Y-tunnus tai
   nimi + kotikunta).
3. Etusivulta ja yhteystietosivuilta poimitaan sähköpostit, puhelinnumerot ja
   yhteyshenkilö (nimi + titteli, tai etunimi.sukunimi@-osoite). robots.txt huomioidaan.
4. Hyväksytään vain rivit, joilla on nimi, Y-tunnus, yhteyshenkilö, puhelin ja sähköposti.
5. Jo kontaktoidut suodatetaan pois erikseen (Gmailin lähetetyt + --exclude-tiedosto
   domaineista/osoitteista), ks. README.

Ajo:  python scripts/prospecting/crawl_prospects.py --target 1300 --out exports/prospektit
Tila (välimuistit, jo käsitellyt Y-tunnukset) säilyy out-hakemistossa, joten ajon voi jatkaa.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import html
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

PRH_URL = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"
DOH_URL = "https://cloudflare-dns.com/dns-query"
UA = "Mozilla/5.0 (compatible; FlowLeadsProspector/1.0)"

# Toimialat (PRH käyttää TOL 2025 -luokitusta 2026 alkaen; 4 ensimmäistä numeroa) tärkeysjärjestyksessä: FlowLeadsin remonttityypit.
BUSINESS_LINES = [
    ("4322", "LVI / putkiremontti"),
    ("4321", "Sähkö"),
    ("4341", "Katto"),
    ("4334", "Maalaus / julkisivu"),
    ("4323", "Eristys"),
    ("4324", "Muu rakennusasennus"),
    ("4335", "Muu viimeistely"),
    ("4342", "Muu erikoistunut talonrakentaminen"),
    ("4311", "Purku / asbesti"),
    ("4331", "Rappaus"),
    ("4333", "Lattia- ja seinäpäällysteet"),
    ("4332", "Rakennuspuusepän asennus"),
    ("4399", "Muu erikoistunut rakentaminen"),
    ("4100", "Rakentaminen / saneeraus"),
    ("8122", "IV-puhdistus / rakennussiivous"),
]
# 16 = Osakeyhtiö, 5 = Kommandiittiyhtiö, 4 = Avoin yhtiö, 14 = Osuuskunta
ALLOWED_FORMS = {"16", "5", "4", "14"}

CONTACT_HINTS = ("yhteys", "ota-yhteytta", "otayhteytta", "contact", "yhteystiedot",
                 "henkilokunta", "henkilosto", "meista", "yritys", "tiimi", "about")
TITLE_WORDS = ("toimitusjohtaja", "yrittäjä", "omistaja", "myyntipäällikkö", "myynti",
               "projektipäällikkö", "työnjohtaja", "vastaava työnjohtaja", "aluepäällikkö",
               "johtaja", "toimisto", "ceo", "osakas", "hallituksen puheenjohtaja")
TITLE_RANK = {"toimitusjohtaja": 0, "ceo": 0, "yrittäjä": 1, "omistaja": 1, "osakas": 2,
              "myyntipäällikkö": 2, "johtaja": 3, "aluepäällikkö": 3, "projektipäällikkö": 4,
              "vastaava työnjohtaja": 4, "työnjohtaja": 5, "myynti": 5,
              "hallituksen puheenjohtaja": 2, "toimisto": 6}
FREE_MAIL = ("gmail.com", "outlook.com", "hotmail.com", "luukku.com", "kolumbus.fi",
             "elisanet.fi", "netti.fi", "suomi24.fi", "yahoo.com", "live.com", "icloud.com",
             "saunalahti.fi", "pp.inet.fi", "welho.com", "dnainternet.net", "msn.com")
BAD_EMAIL_PARTS = ("example", "etunimi.etunimi", "firstname.firstname", "sentry", "wixpress", "domain.", "yourname", "email.com",
                   "@sentry", "noreply", "no-reply", ".png", ".jpg", ".webp", ".gif", ".svg")
NAME_STOP = {"oy", "ab", "ky", "ltd", "oyj", "tmi", "ay", "osk", "ry", "finland", "suomi",
             "group", "the", "ja", "and"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
OBF_RE = re.compile(r"([A-Za-z0-9._%+\-]+)\s*(?:\(at\)|\[at\]|\{at\}| at |\(a\)|\[ät\]|＠)\s*"
                    r"([A-Za-z0-9\-]+(?:\s*(?:\.|\(dot\)|\[dot\]|\(piste\))\s*[A-Za-z0-9\-]+)+)", re.I)
PHONE_RE = re.compile(r"(?<![\d+])(?:\+358|00358|0)[\s\-]?\(?0?\)?[1-9]\d{0,2}[\s\-]?\d{2,4}[\s\-]?\d{2,4}(?:[\s\-]?\d{1,4})?(?!\d)")
YTUNNUS_RE = re.compile(r"\b(\d{7})\s*[-‐–]\s*(\d)\b")
NAME_TOKEN = r"[A-ZÅÄÖÜ][a-zåäöüéè]+(?:-[A-ZÅÄÖ][a-zåäöé]+)?"
PERSON_RE = re.compile(rf"\b({NAME_TOKEN}) ({NAME_TOKEN})\b")

NOT_PERSON = {"Ota", "Yhteystiedot", "Puhelin", "Sähköposti", "Oy", "Ab", "Ky", "Lue", "Tilaa",
              "Kaikki", "Etusivu", "Palvelut", "Meistä", "Yritys", "Toimitusjohtaja", "Myynti",
              "Asiakaspalvelu", "Laskutus", "Huolto", "Arvonlisäverollinen", "Tietosuoja",
              "Suomen", "Hinta", "Tarjous", "Pyydä", "Soita", "Lähetä", "Osoite", "Avoinna",
              "Palvelumme", "Referenssit", "Uutiset", "Copyright", "Kotitalousvähennys", "Keski",
              "Etelä", "Pohjois", "Itä", "Länsi", "Uusimaa", "Pirkanmaa", "Helsinki", "Espoo",
              "Vantaa", "Tampere", "Turku", "Oulu", "Jyväskylä", "Lahti", "Kuopio", "Pori",
              "Rakennus", "Remontti", "Sähkö", "Putki", "Katto", "Maalaus", "Saneeraus", "Talo",
              "Lämpö", "Vesi", "Ilmanvaihto", "Uusi", "Lisää", "Kysy", "Hae", "Katso", "Info",
              "Toimisto", "Työnjohtaja", "Projektipäällikkö", "Yrittäjä", "Omistaja", "Johtaja",
              "Vastaava", "Myyntipäällikkö", "Puh", "Tel", "Email", "Mail", "Gsm", "Puhelinnumero",
              "Seuraa", "Facebook", "Instagram", "Linkedin", "Kesä", "Talvi", "Syys", "Kevät",
              "Hyvä", "Hyvää", "Kiitos", "Tervetuloa", "Teemme", "Olemme", "Meidän", "Kun", "Jos",
              "Asunto", "Taloyhtiö", "Taloyhtiöt", "Kiinteistö", "Kiinteistöt", "Yhteydenotto",
              "Lomake", "Viesti", "Nimi", "Etunimi", "Sukunimi", "Ilmainen", "Arvio", "Kartoitus"}


def norm(s: str) -> str:
    s = s.lower().replace("&", " ja ")
    s = s.replace("ä", "a").replace("ö", "o").replace("å", "a").replace("ü", "u")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s


def name_tokens(name: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", norm(name))
    return [t for t in toks if t not in NAME_STOP]


def domain_guesses(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        toks = name_tokens(n)
        if not toks:
            continue
        joined = "".join(toks)
        hyph = "-".join(toks)
        cands = [joined, hyph]
        if len(toks) > 1:
            cands.append(toks[0] + "".join(toks[1:2]))
        cands += [joined + "oy", hyph + "-oy"]
        for c in cands:
            if 3 <= len(c) <= 40:
                for tld in (".fi", ".com"):
                    d = c + tld
                    if d not in out:
                        out.append(d)
    return out[:8]


def clean_phone(p: str) -> str:
    digits = re.sub(r"[^\d+]", "", p)
    if digits.startswith("00358"):
        digits = "+358" + digits[5:]
    while digits.startswith("+3580"):
        digits = "+358" + digits[5:]
    if digits.startswith("0"):
        digits = "+358" + digits[1:]
    return digits


def valid_phone(p: str) -> bool:
    d = clean_phone(p)
    return d.startswith("+358") and 11 <= len(d) <= 13


def is_mobile(p: str) -> bool:
    return clean_phone(p)[4:5] == "4" or clean_phone(p)[4:6] == "50"


def ok_email(e: str) -> bool:
    e = e.lower()
    return not any(b in e for b in BAD_EMAIL_PARTS) and len(e) < 80


def email_domain(e: str) -> str:
    return e.split("@", 1)[1].lower()


def person_from_email(e: str) -> str | None:
    local = e.split("@", 1)[0].lower()
    m = re.fullmatch(r"([a-zåäö]{2,})[._]([a-zåäö]{2,})", local)
    if not m:
        return None
    a, b = m.groups()
    generic = {"info", "myynti", "toimisto", "asiakaspalvelu", "laskutus", "huolto",
               "tarjous", "tarjoukset", "palvelu", "posti", "office", "sales", "admin",
               "tilaukset", "tyomaa", "kirjanpito", "rekry", "hr", "contact"}
    if a in generic or b in generic:
        return None
    return f"{a.capitalize()} {b.capitalize()}"


FIRST_NAMES = {l.strip() for l in (Path(__file__).parent / "etunimet.txt").read_text(encoding="utf-8").splitlines()
               if l.strip()} if (Path(__file__).parent / "etunimet.txt").exists() else set()

GENERIC_LOCAL = ("info", "myynti", "toimisto", "yhteys", "asiakaspalvelu", "tarjous", "tarjoukset",
                 "palvelu", "posti", "office", "sales", "contact", "tilaukset")


def validate_row(r: dict) -> dict | None:
    """Final consistency checks; returns the (possibly corrected) row or None."""
    email = r["sahkoposti"].lower()
    contact0 = r["yhteyshenkilo"]
    if email.split("@")[0] in ("etunimi.sukunimi", "firstname.lastname") and " " in contact0 \
            and "etunimi" not in contact0.lower() and "firstname" not in contact0.lower():
        fn, ln = (re.sub(r"[^a-z-]", "", norm(x)) for x in contact0.split(" ", 1))
        email = f"{fn}.{ln}@{email.split('@')[1]}"
        r["sahkoposti"] = email
    if any(w in email or w in contact0.lower() for w in ("etunimi", "sukunimi", "firstname", "lastname")):
        return None
    if not ok_email(email) or not valid_phone(r["puhelin"]):
        return None
    r["puhelin"] = clean_phone(r["puhelin"])
    contact = r["yhteyshenkilo"]
    company_toks = set(name_tokens(r["nimi"])) | {t for n in str(r.get("aputoiminimet", "")).split(";")
                                                  for t in name_tokens(n)}
    if set(name_tokens(contact)) & company_toks and len(name_tokens(contact)) >= 2 \
            and set(name_tokens(contact)) <= company_toks | {"oy"}:
        return None  # "yhteyshenkilö" on yrityksen nimi
    local = email.split("@")[0]
    ep = person_from_email(email)
    if ep and local not in GENERIC_LOCAL:
        fn, ln = (norm(x) for x in contact.split(" ", 1)) if " " in contact else (norm(contact), "")
        if not (fn in local and ln[:4] in local):
            # osoite kuuluu eri henkilölle: käytetään osoitteen henkilöä yhteyshenkilönä
            r["yhteyshenkilo"], r["titteli"] = ep, ""
    # nimen pitää olla joko sähköpostiosoitteen vahvistama tai alkaa tunnetulla etunimellä
    name = r["yhteyshenkilo"]
    fn, _, ln = name.partition(" ")
    confirmed = norm(fn) in local and norm(ln)[:4] in local
    if not confirmed and FIRST_NAMES and fn.split("-")[0] not in FIRST_NAMES and fn not in FIRST_NAMES:
        return None
    return r


def page_text(soup: BeautifulSoup) -> str:
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    return re.sub(r"[ \t\r\f\v]+", " ", soup.get_text("\n"))


def find_people(text: str) -> list[tuple[int, str, str]]:
    """(rank, name, title) from lines near a title word."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    found: list[tuple[int, str, str]] = []
    for i, line in enumerate(lines):
        low = line.lower()
        for title in TITLE_WORDS:
            if title in low and len(line) < 120:
                window = lines[max(0, i - 2): i + 3]
                for w in window:
                    for m in PERSON_RE.finditer(w):
                        a, b = m.groups()
                        if a in NOT_PERSON or b in NOT_PERSON:
                            continue
                        found.append((TITLE_RANK.get(title, 7), f"{a} {b}", title))
                break
    return found


class Crawler:
    def __init__(self, out: Path, concurrency: int, exclude: set[str]):
        self.out = out
        self.sem = asyncio.Semaphore(concurrency)
        self.exclude = exclude
        self.state_path = out / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"done": []}
        self.done = set(self.state["done"])
        self.results_path = out / "prospektit_raw.jsonl"
        self.results: dict[str, dict] = {}
        if self.results_path.exists():
            for line in self.results_path.read_text().splitlines():
                r = json.loads(line)
                self.results[r["y_tunnus"]] = r
        self.client = httpx.AsyncClient(headers={"User-Agent": UA, "Accept-Language": "fi"},
                                        timeout=httpx.Timeout(8, connect=5),
                                        limits=httpx.Limits(max_connections=500, max_keepalive_connections=100),
                                        follow_redirects=True, verify=True)
        self.dns_cache: dict[str, bool] = {}
        self.dns_errors = 0
        self.last_hit = 0
        self.stale_clients: list[httpx.AsyncClient] = []
        self.dns_client = httpx.AsyncClient(timeout=httpx.Timeout(6),
                                            limits=httpx.Limits(max_connections=48))
        self.dns_sem = asyncio.Semaphore(48)
        self.web_sem = asyncio.Semaphore(150)

    # ---------- PRH ----------
    async def prh_page(self, line: str, page: int) -> dict:
        cache = self.out / "prh" / f"{line}_{page}.json"
        if cache.exists():
            return json.loads(cache.read_text())
        for attempt in range(5):
            try:
                r = await self.client.get(PRH_URL, params={"mainBusinessLine": line, "page": page})
                if r.status_code == 200:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(r.text)
                    return r.json()
                await asyncio.sleep(2 + attempt * 3)
            except httpx.HTTPError:
                await asyncio.sleep(2 + attempt * 3)
        return {"companies": [], "totalResults": 0}

    @staticmethod
    def parse_company(c: dict, line_label: str) -> dict | None:
        if c.get("status") != "2" or c.get("companySituations"):
            return None
        forms = {f["type"] for f in c.get("companyForms", []) if not f.get("endDate")}
        if not forms & ALLOWED_FORMS:
            return None
        names = [n for n in c.get("names", []) if not n.get("endDate")]
        main = next((n["name"] for n in names if n.get("type") == "1"), None)
        if not main:
            return None
        aux = [n["name"] for n in names if n.get("type") in ("3", "2") and n["name"] != main]
        addr = next((a for a in c.get("addresses", []) if a.get("type") == 1), None) or \
            next(iter(c.get("addresses", [])), None) or {}
        offices = addr.get("postOffices") or [{}]
        city = next((o.get("city", "") for o in offices if o.get("languageCode") == "1"), offices[0].get("city", ""))
        street = " ".join(x for x in [addr.get("street", ""), addr.get("buildingNumber", "")] if x)
        web = (c.get("website") or {}).get("url")
        return {
            "y_tunnus": c["businessId"]["value"], "nimi": main, "aputoiminimet": aux,
            "toimiala": line_label, "toimialakoodi": (c.get("mainBusinessLine") or {}).get("type", ""),
            "osoite": street, "postinumero": addr.get("postCode", ""), "kunta": city.title(),
            "rekisteroity": c.get("registrationDate", ""), "prh_www": web,
        }

    # ---------- web ----------
    async def resolves(self, host: str) -> bool:
        """DNS over HTTPS. Errors are retried on another resolver and never cached as 'no domain'."""
        if host in self.dns_cache:
            return self.dns_cache[host]
        for attempt in range(4):
            url = DOH_URL if attempt % 2 == 0 else "https://dns.google/resolve"
            try:
                async with self.dns_sem:
                    r = await self.dns_client.get(url, params={"name": host, "type": "A"},
                                                  headers={"accept": "application/dns-json"})
                if r.status_code == 200:
                    data = r.json()
                    if data.get("Status") in (0, 2, 3, 5):
                        ok = bool(data.get("Answer"))
                        self.dns_cache[host] = ok
                        return ok
            except Exception:
                pass
            self.dns_errors += 1
            await asyncio.sleep(1 + attempt * 2)
        raise RuntimeError("dns unavailable")

    async def fetch(self, url: str) -> tuple[str, str] | None:
        try:
            async with self.web_sem:
                r = await self.client.get(url)
        except Exception:
            return None
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "html" not in ctype:
            return None
        return str(r.url), r.text[:1_500_000]

    async def robots(self, base: str) -> RobotFileParser:
        rp = RobotFileParser()
        try:
            async with self.web_sem:
                r = await self.client.get(urljoin(base, "/robots.txt"))
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp.parse([])
        return rp

    def matches_company(self, text: str, co: dict) -> bool:
        if co["y_tunnus"] in text or co["y_tunnus"].replace("-", "") in text:
            return True
        t = norm(text)
        for n in [co["nimi"]] + co["aputoiminimet"]:
            toks = name_tokens(n)
            if not toks:
                continue
            core = " ".join(toks)
            if len(core) >= 5 and re.search(r"\b" + re.escape(core) + r"\b", t):
                if not co["kunta"] or norm(co["kunta"]) in t or len(toks) >= 2:
                    return True
        return False

    async def site_for(self, co: dict) -> str | None:
        cands: list[str] = []
        if co["prh_www"]:
            u = co["prh_www"].strip()
            cands.append(u if u.startswith("http") else "https://" + u.lstrip("/"))
        for d in domain_guesses([co["nimi"]] + co["aputoiminimet"]):
            cands.append("https://" + d)
        hosts = []
        for url in cands:
            host = urlparse(url).hostname or ""
            hosts.append(host[4:] if host.startswith("www.") else host)
        checks = await asyncio.gather(*(self.resolves(h) for h in hosts))
        for url, ok in zip(cands, checks):
            if not ok:
                continue
            got = await self.fetch(url) or await self.fetch(url.replace("https://", "https://www.", 1)) \
                or await self.fetch(url.replace("https://", "http://", 1))
            if not got:
                continue
            final, body = got
            text = page_text(BeautifulSoup(body, "html.parser"))
            co["_home"] = (final, body)
            if url == cands[0] and co["prh_www"]:
                return final  # PRH:n ilmoittama sivu kelpaa sellaisenaan
            if self.matches_company(text, co):
                return final
            co["_pending_verify"] = final
            # tarkistetaan vielä yhteystietosivulta (Y-tunnus on usein siellä)
            return final
        return None

    async def scrape(self, co: dict) -> dict | None:
        site = await self.site_for(co)
        if not site:
            return None
        rp = await self.robots(site)
        home_url, home_html = co.pop("_home")
        pages = [(home_url, home_html)]
        soup = BeautifulSoup(home_html, "html.parser")
        site_host = (urlparse(home_url).hostname or "").removeprefix("www.")
        links: list[str] = []
        for a in soup.find_all("a", href=True):
            href = urljoin(home_url, a["href"]).split("#")[0]
            h = urlparse(href)
            if (h.hostname or "").removeprefix("www.") != site_host:
                continue
            key = norm(h.path + " " + a.get_text(" "))
            if any(k in key for k in CONTACT_HINTS) and href not in links and href != home_url:
                links.append(href)
        links.sort(key=lambda u: 0 if ("yhteys" in u or "contact" in u) else 1)
        for extra in ("/yhteystiedot", "/ota-yhteytta"):
            u = urljoin(home_url, extra)
            if u not in links:
                links.append(u)
        for u in links[:5]:
            if not rp.can_fetch(UA, u):
                continue
            got = await self.fetch(u)
            if got:
                pages.append(got)

        all_text, emails, phones, people, mailtos = "", [], [], [], []
        for _, body in pages:
            s = BeautifulSoup(body, "html.parser")
            for a in s.find_all("a", href=True):
                h = a["href"]
                if h.lower().startswith("mailto:"):
                    mailtos.append(html.unescape(h[7:].split("?")[0]).strip())
                elif h.lower().startswith("tel:"):
                    phones.append(h[4:])
            txt = page_text(s)
            all_text += "\n" + txt
            emails += EMAIL_RE.findall(txt)
            for m in OBF_RE.finditer(txt):
                dom = re.sub(r"\s*(?:\(dot\)|\[dot\]|\(piste\))\s*", ".", m.group(2)).replace(" ", "")
                emails.append(f"{m.group(1)}@{dom}")
            phones += PHONE_RE.findall(YTUNNUS_RE.sub(" ", txt))
            people += find_people(txt)

        if co.pop("_pending_verify", None) and not self.matches_company(all_text, co):
            return None
        emails = [e.strip(".").lower() for e in mailtos + emails if ok_email(e)]
        emails = list(dict.fromkeys(e for e in emails if EMAIL_RE.fullmatch(e)))
        own = [e for e in emails if email_domain(e) == site_host or email_domain(e).endswith("." + site_host)]
        free = [e for e in emails if email_domain(e) in FREE_MAIL]
        emails = own or free or [e for e in emails if not email_domain(e).endswith(("prh.fi", "vero.fi"))][:0]
        phones = list(dict.fromkeys(clean_phone(p) for p in phones if valid_phone(p)))
        if not emails or not phones:
            return None

        # yhteyshenkilö: titteli + nimi, muuten etunimi.sukunimi@-osoite
        people.sort(key=lambda x: x[0])
        contact, title = None, ""
        if people:
            _, contact, title = people[0]
        person_email = None
        if contact:
            fn, ln = (norm(x) for x in contact.split(" ", 1))
            for e in emails:
                loc = e.split("@")[0]
                if fn in loc and ln[:4] in loc:
                    person_email = e
                    break
        if not contact:
            for e in emails:
                p = person_from_email(e)
                if p:
                    contact, person_email, title = p, e, ""
                    break
        if not contact:
            return None
        email = person_email or next((e for e in emails if e.split("@")[0] in GENERIC_LOCAL), emails[0])
        # puhelin: yhteyshenkilön lähellä oleva numero, muuten ensimmäinen (mieluiten matkapuhelin)
        phone = phones[0]
        idx = all_text.find(contact)
        if idx >= 0:
            near = PHONE_RE.findall(YTUNNUS_RE.sub(" ", all_text[idx: idx + 300]))
            near = [clean_phone(p) for p in near if valid_phone(p)]
            if near:
                phone = near[0]
        elif any(is_mobile(p) for p in phones):
            phone = next(p for p in phones if is_mobile(p))
        return validate_row({
            **{k: v for k, v in co.items() if not k.startswith("_")},
            "aputoiminimet": "; ".join(co["aputoiminimet"]),
            "yhteyshenkilo": contact, "titteli": title, "puhelin": phone, "sahkoposti": email,
            "muut_sahkopostit": "; ".join(e for e in emails if e != email)[:300],
            "muut_puhelimet": "; ".join(p for p in phones if p != phone)[:200],
            "verkkosivu": home_url, "domain": site_host,
        })

    async def handle(self, co: dict) -> None:
        if True:
            try:
                res = await asyncio.wait_for(self.scrape(co), timeout=60)
            except (RuntimeError, asyncio.TimeoutError, TimeoutError):
                return  # DNS ei vastannut / aikakatkaisu: ei merkitä käsitellyksi, yritetään seuraavalla ajolla
            except Exception:
                res = None
            self.done.add(co["y_tunnus"])
            if len(self.done) % 20 == 0:
                (self.out / "heartbeat").touch()
            if res:
                self.last_hit = len(self.done)
            elif len(self.done) - self.last_hit > 400:
                # 400 peräkkäistä ilman tulosta: yhteyspooli on todennäköisesti rikki -> uusi client
                print("  vahtikoira: luodaan HTTP-client uudelleen", flush=True)
                self.last_hit = len(self.done)
                old, old_dns = self.client, self.dns_client
                self.client = httpx.AsyncClient(headers=old.headers, timeout=httpx.Timeout(8, connect=5),
                                                follow_redirects=True,
                                                limits=httpx.Limits(max_connections=500, max_keepalive_connections=100))
                self.dns_client = httpx.AsyncClient(timeout=httpx.Timeout(6), limits=httpx.Limits(max_connections=20))
                self.stale_clients.extend([old, old_dns])
            if len(self.done) % 100 == 0:
                self.save_state()
                print(f"  käsitelty {len(self.done)}, hyväksyttyjä {len(self.results)}, "
                      f"DNS-virheitä {self.dns_errors}", flush=True)
            if res:
                d = res["domain"]
                if d in self.exclude or res["sahkoposti"] in self.exclude:
                    return
                if any(r["domain"] == d and d not in FREE_MAIL for r in self.results.values()):
                    return  # sama yritys toisella Y-tunnuksella
                self.results[res["y_tunnus"]] = res
                with self.results_path.open("a") as f:
                    f.write(json.dumps(res, ensure_ascii=False) + "\n")

    def save_state(self) -> None:
        self.state["done"] = sorted(self.done)
        self.state_path.write_text(json.dumps(self.state))

    async def run(self, target: int, max_pages: int, workers: int = 60) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=workers * 4)
        stop = asyncio.Event()
        seen = 0

        async def worker() -> None:
            while True:
                co = await queue.get()
                if co is None:
                    queue.task_done()
                    return
                if not stop.is_set():
                    await self.handle(co)
                    if len(self.results) >= target:
                        stop.set()
                queue.task_done()

        async def producer() -> None:
            nonlocal seen
            for line, label in BUSINESS_LINES:
                for page in range(1, max_pages + 1):
                    if stop.is_set():
                        return
                    data = await self.prh_page(line, page)
                    comps = data.get("companies", [])
                    if not comps:
                        break
                    for c in comps:
                        p = self.parse_company(c, label)
                        if p and p["y_tunnus"] not in self.done:
                            seen += 1
                            await queue.put(p)
                    if page % 5 == 0:
                        self.save_state()
                        print(f"[{label} s.{page}] käsitelty {len(self.done)}, "
                              f"hyväksyttyjä {len(self.results)}", flush=True)

        tasks = [asyncio.create_task(worker()) for _ in range(workers)]
        await producer()
        for _ in tasks:
            await queue.put(None)
        await asyncio.gather(*tasks)
        self.save_state()
        await self.client.aclose()
        await self.dns_client.aclose()


def write_csv(results: list[dict], path: Path) -> None:
    cols = ["nimi", "y_tunnus", "yhteyshenkilo", "titteli", "puhelin", "sahkoposti", "verkkosivu",
            "toimiala", "toimialakoodi", "osoite", "postinumero", "kunta", "aputoiminimet",
            "rekisteroity", "muut_sahkopostit", "muut_puhelimet", "domain"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", delimiter=";")
        w.writeheader()
        for r in results:
            w.writerow(r)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1300)
    ap.add_argument("--out", default="exports/prospektit")
    ap.add_argument("--concurrency", type=int, default=40)
    ap.add_argument("--max-pages", type=int, default=400)
    ap.add_argument("--exclude", help="tiedosto: yksi domain tai sähköposti per rivi")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    exclude: set[str] = set()
    if a.exclude and Path(a.exclude).exists():
        exclude = {l.strip().lower() for l in Path(a.exclude).read_text().splitlines() if l.strip()}
    c = Crawler(out, a.concurrency, exclude)
    asyncio.run(c.run(a.target, a.max_pages, a.concurrency))
    rows = [v for r in c.results.values() if (v := validate_row(dict(r)))]
    write_csv(rows, out / "prospektit_kaikki.csv")
    print(f"Valmis: {len(rows)} prospektia -> {out / 'prospektit_kaikki.csv'}")


if __name__ == "__main__":
    sys.exit(main())
