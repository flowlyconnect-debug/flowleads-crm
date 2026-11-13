"""The public price list. Prices are monthly, VAT 0 % (ALV lisätään laskulle).

Change the prices and contents here; /hinnasto, the Tilaus page and the admin
customer list all read this module. `max_users` None = no limit.
"""

from __future__ import annotations

TRIAL_DAYS = 14
VAT_PERCENT = "25,5"

PLANS: tuple[dict, ...] = (
    {
        "key": "flowleads",
        "name": "FlowLeads",
        "price": 149,
        "tagline": "Kaikki ominaisuudet yhteen kuukausihintaan",
        "max_users": None,
        "highlight": True,
        "features": (
            "Kaikki urakkatyypit koko Suomesta",
            "Kohteet pisteytettynä, kartalla ja taloyhtiökorteilla",
            "Isännöitsijät, hallituksen puheenjohtajat ja huoltoyhtiöt",
            "Myyntiputki, tehtävät, sähköposti, tarjoukset ja sekvenssit",
            "Vienti HubSpotiin ja Pipedriveen",
            "Haku tavallisella kielellä, tallennetut haut ja kuukausiraportti",
            "Rajaton määrä käyttäjiä",
        ),
    },
)

PLANS_BY_KEY = {plan["key"]: plan for plan in PLANS}
# what a trial gets: everything of the most complete priced plan
TRIAL_PLAN = "flowleads"

TERMS = (
    f"Kokeilujakso kestää {TRIAL_DAYS} päivää. Se on maksuton eikä vaadi maksukorttia, eikä se muutu maksulliseksi itsestään.",
    "Kuukausisopimus: ei määräaikaa eikä aloitusmaksua. Lasku lähetetään kuukausittain etukäteen.",
    "Voit irtisanoa sopimuksen milloin tahansa palvelusta. Palvelu on käytössäsi kuluvan kuukauden loppuun, eikä seuraavaa kuukautta laskuteta.",
    "Hinnat ovat arvonlisäverottomia (ALV " + VAT_PERCENT + " % lisätään laskulle).",
    "Omat tietosi (liidit, muistiinpanot, tarjoukset) voit viedä ulos milloin tahansa, myös sopimuksen päätyttyä.",
)


def price_label(plan: dict) -> str:
    return "Sopimuksen mukaan" if plan.get("price") is None else f"{plan['price']} € / kk"
