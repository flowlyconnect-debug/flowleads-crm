"""Oikotie / property metadata display helpers for lead detail."""

PROPERTY_INFO_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("taloyhtio", "Taloyhtiö", False),
    ("isannoitsija_yritys", "Isännöitsijäyritys", False),
    ("isannoitsija_puh", "Isännöitsijän puhelin", False),
    ("isannoitsija_email", "Isännöitsijän sähköposti", False),
    ("isannoitsija_ytunnus", "Y-tunnus", False),
    ("remonttityyppi", "Remonttityyppi", False),
    ("tulevat_remontit", "Tulevat remontit", False),
    ("kaupunki", "Kaupunki", False),
    ("osoite", "Osoite", False),
    ("hinta", "Hinta", False),
    ("oikotie_url", "Oikotie-linkki", True),
)


def _has_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def get_property_info_rows(ai_contact_info: dict | None) -> list[dict]:
    """Return non-empty property fields for CRM detail display."""
    if not ai_contact_info:
        return []

    rows: list[dict] = []
    for key, label, is_url in PROPERTY_INFO_FIELDS:
        value = ai_contact_info.get(key)
        if not _has_value(value):
            continue
        text = str(value).strip()
        row: dict = {"key": key, "label": label, "value": text}
        if is_url:
            row["is_url"] = True
            row["link_text"] = "Avaa Oikotiessä"
        else:
            row["is_url"] = False
        rows.append(row)
    return rows
