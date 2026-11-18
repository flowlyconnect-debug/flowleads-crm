"""One form for contact data, whatever the source wrote (n8n, CSV imports):
phone numbers, e-mail addresses and property-manager office names.

Applied when a listing is saved (`Listing` validators) and to older rows by
`normalize_old_contacts()` in the `listing_matching` job. Nothing is thrown
away: a value that does not look like a phone number is kept as written."""

from __future__ import annotations

import re

_PHONE_PREFIX = re.compile(r"^\s*(puh(elin)?\.?|tel\.?|p\.|gsm|mob\.?)\s*:?\s*", re.IGNORECASE)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$")
_COMPANY_FORMS = re.compile(r"\b(oy|oyj|ab|ky|ay|tmi|ltd|abp)\b\.?", re.IGNORECASE)


def normalize_phone(value: str | None) -> str | None:
    """Finnish numbers in one form: mobile "040 123 4567", landline "09 123 4567",
    international ones "+46 ..."; the first number when several are given."""
    if value is None:
        return None
    text = _PHONE_PREFIX.sub("", str(value)).strip()
    if not text:
        return None
    text = re.split(r"[,;/]| tai | or ", text)[0].strip()
    digits = re.sub(r"\D", "", text)
    if text.startswith("+") and not digits.startswith("358"):
        return "+" + digits if len(digits) >= 7 else text  # a foreign number: digits only
    if digits.startswith("00358"):
        digits = "0" + digits[5:]
    elif digits.startswith("358"):
        digits = "0" + digits[3:]
    if not digits.startswith("0") or not 7 <= len(digits) <= 12:
        return text  # not a Finnish number we recognise: keep it as written
    # three-digit prefixes: mobile (04x, 050), nationwide (010, 020, 029, 030, 07x, 0800...) and the
    # 013-019 area codes; two digits for the other areas (02, 03, 05, 06, 08, 09), whose numbers never start with 0
    head = 3 if digits[1] in "147" or digits[2] == "0" or digits.startswith("029") else 2
    rest = digits[head:]
    groups = [rest[:3], rest[3:]] if len(rest) > 4 else [rest]
    return " ".join([digits[:head], *[g for g in groups if g]])


def normalize_email(value: str | None) -> str | None:
    """Lower case, "(at)" written out, "mailto:" dropped; None when it is not an address."""
    if value is None:
        return None
    text = str(value).strip().lower()
    text = re.sub(r"^mailto:", "", text)
    text = re.sub(r"\s*[\(\[]\s*(at|ät)\s*[\)\]]\s*", "@", text)
    text = re.sub(r"\s*[\(\[]\s*(dot|piste)\s*[\)\]]\s*", ".", text)
    text = text.strip(" .;,<>")
    if not text:
        return None
    return text if _EMAIL.match(text) else None


def manager_key(name: str | None) -> str:
    """Property-manager offices by name, ignoring case, spacing and the
    company form: "Ehta Isännöinti Oy" and "Ehta isännöinti" are one office."""
    text = _COMPANY_FORMS.sub(" ", (name or "").lower())
    return " ".join(re.sub(r"[.,]", " ", text).split())


PHONE_FIELDS = ("isannoitsija_puh", "hallitus_pj_puh", "huoltoyhtio_puh")
EMAIL_FIELDS = ("isannoitsija_email", "hallitus_pj_email", "huoltoyhtio_email")


def normalize_old_contacts(limit: int = 2000) -> int:
    """Listings stored before the normalisation (scheduler). Returns rows changed."""
    from sqlalchemy import or_

    from app.extensions import db
    from app.listings.models import Listing

    columns = [getattr(Listing, f) for f in PHONE_FIELDS + EMAIL_FIELDS]
    rows = (
        Listing.query.filter(or_(*[c.isnot(None) for c in columns]), Listing.contacts_normalized_at.is_(None))
        .limit(limit)
        .all()
    )
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    changed = 0
    for listing in rows:
        before = [getattr(listing, f) for f in PHONE_FIELDS + EMAIL_FIELDS]
        for field in PHONE_FIELDS + EMAIL_FIELDS:
            setattr(listing, field, getattr(listing, field))  # the validators normalise
        listing.contacts_normalized_at = now
        if [getattr(listing, f) for f in PHONE_FIELDS + EMAIL_FIELDS] != before:
            changed += 1
    if rows:
        db.session.commit()
    return changed
