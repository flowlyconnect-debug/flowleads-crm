"""The public price list. Prices are monthly, VAT 0 % (ALV lisätään laskulle).

Change the prices and contents here; /hinnasto, the Tilaus page and the admin
customer list all read this module. `max_users` None = no limit.
"""

from __future__ import annotations

TRIAL_DAYS = 14
VAT_PERCENT = "25,5"

PLANS: tuple[dict, ...] = (
    {
        "key": "perus",
        "name": "Perus",
        "price": 149,
        "tagline": "Yhden urakkatyypin urakoitsijalle",
        "max_users": 2,
        "features": (
            "1 urakkatyyppi koko Suomesta",
            "Kohteet pisteytettynä, kartalla ja taloyhtiökorteilla",
            "Isännöitsijät, hallituksen puheenjohtajat ja huoltoyhtiöt",
            "Myyntiputki, tehtävät ja sähköposti",
            "Kuukausiraportti",
            "2 käyttäjää",
        ),
    },
    {
        "key": "ammattilainen",
        "name": "Ammattilainen",
        "price": 290,
        "tagline": "Myyntitiimille, jolla on useampi urakka",
        "max_users": 5,
        "highlight": True,
        "features": (
            "Kaikki urakkatyypit",
            "Kaikki Perus-paketin ominaisuudet",
            "Vienti HubSpotiin ja Pipedriveen",
            "Tarjoukset, sekvenssit ja automaatiot",
            "Haku tavallisella kielellä ja tallennetut haut",
            "5 käyttäjää",
        ),
    },
    {
        "key": "yritys",
        "name": "Yritys",
        "price": None,  # by agreement
        "tagline": "Isommalle organisaatiolle ja omille tietolähteille",
        "max_users": None,
        "features": (
            "Kaikki Ammattilainen-paketin ominaisuudet",
            "Rajaton määrä käyttäjiä",
            "REST-rajapinta ja webhookit",
            "Omien kohdelistojen tuonti",
            "Käyttöönotto ja koulutus",
        ),
    },
)

PLANS_BY_KEY = {plan["key"]: plan for plan in PLANS}
# what a trial gets: everything of the most complete priced plan
TRIAL_PLAN = "ammattilainen"

TERMS = (
    f"Kokeilujakso kestää {TRIAL_DAYS} päivää. Se on maksuton eikä vaadi maksukorttia, eikä se muutu maksulliseksi itsestään.",
    "Kuukausisopimus: ei määräaikaa eikä aloitusmaksua. Lasku lähetetään kuukausittain etukäteen.",
    "Voit irtisanoa sopimuksen milloin tahansa palvelusta. Palvelu on käytössäsi kuluvan kuukauden loppuun, eikä seuraavaa kuukautta laskuteta.",
    "Hinnat ovat arvonlisäverottomia (ALV " + VAT_PERCENT + " % lisätään laskulle).",
    "Omat tietosi (liidit, muistiinpanot, tarjoukset) voit viedä ulos milloin tahansa, myös sopimuksen päätyttyä.",
)


def price_label(plan: dict) -> str:
    return "Sopimuksen mukaan" if plan.get("price") is None else f"{plan['price']} € / kk"
