"""Keys that tell whether two rows from different sources are the same
housing company or the same street address. Shared by every source (the
listing catalogue, the public registers and any later one), so they all
identify a taloyhtiö the same way (app/listings/housing_registry.py)."""

from __future__ import annotations

import re

from app.listings.housing import fold, normalize_company_name, normalize_ytunnus

# "Takojankatu 1c B" and "Sarvijaakonkatu 5b A" too: a letter may follow the number
_STREET_NUMBER = re.compile(r"^(.*?[^\d\s])\s+(\d+)(?!\d)")


def address_key(street, number) -> str | None:
    """"Armas Lindgrenin tie", 19 -> "armaslindgrenintie19": spacing in street
    names differs between registers and company names."""
    street_part = fold(street).replace(" ", "")
    number_part = str(number or "").strip()
    if not street_part or not number_part.isdigit():
        return None
    return f"{street_part}{int(number_part)}"


def street_key(street: str | None) -> str | None:
    """"Koulukatu 5 B 12" -> "koulukatu5": the building, not the apartment."""
    match = _STREET_NUMBER.match(street or "")
    return address_key(match.group(1), match.group(2)) if match else None


def municipality_key(name) -> str:
    return fold(name)


def company_name_key(name) -> str:
    """"Asunto Oy Koivukuja 5" and "As. Oy Koivukuja 5" -> "as oy koivukuja 5"."""
    return normalize_company_name(name)


def business_id(value) -> str | None:
    return normalize_ytunnus(value)
