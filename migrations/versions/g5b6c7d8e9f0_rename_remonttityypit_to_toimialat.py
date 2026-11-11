"""Rename the renovation types to the 12 toimialaa

Revision ID: g5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-11-11 12:00:00.000000

Data only. The five old types that have a direct successor are renamed in
search_profiles, listing_renovations and customer_invites. Rows with an old
type that has no successor ("Sähkö- ja tietoliikennejärjestelmät",
"Huoneistoremontit", "Muu remontti") are left as they are.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "g5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None

RENAMES = {
    "Lukkoremontti / kulunvalvonta": "Lukitus, kulunvalvonta ja oviautomatiikka",
    "Putkiremontti": "Putki- ja viemäriurakointi",
    "Lämmitysjärjestelmän remontti": "Lämmitys- ja energiaremontit",
    "Julkisivuremontti": "Julkisivu- ja parvekeurakointi",
    "Katto- ja vesikattoremontti": "Kattourakointi",
}

TABLES = ("search_profiles", "listing_renovations", "customer_invites")


def _rename(mapping: dict[str, str]) -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in TABLES:
        if table not in existing:
            continue
        for old, new in mapping.items():
            bind.execute(
                sa.text(f"UPDATE {table} SET remonttityyppi = :new WHERE remonttityyppi = :old"),
                {"old": old, "new": new},
            )

    # search_profiles.remonttityypit is a JSON list of type names
    if "search_profiles" not in existing:
        return
    cast = "CAST(:value AS JSONB)" if bind.dialect.name == "postgresql" else ":value"
    rows = bind.execute(
        sa.text("SELECT id, remonttityypit FROM search_profiles WHERE remonttityypit IS NOT NULL")
    ).fetchall()
    for row_id, value in rows:
        types = json.loads(value) if isinstance(value, str) else value
        if not isinstance(types, list):
            continue
        renamed = []
        for name in types:
            name = mapping.get(name, name)
            if name not in renamed:
                renamed.append(name)
        if renamed != types:
            bind.execute(
                sa.text(f"UPDATE search_profiles SET remonttityypit = {cast} WHERE id = :id"),
                {"value": json.dumps(renamed, ensure_ascii=False), "id": row_id},
            )


def upgrade():
    _rename(RENAMES)


def downgrade():
    _rename({new: old for old, new in RENAMES.items()})
