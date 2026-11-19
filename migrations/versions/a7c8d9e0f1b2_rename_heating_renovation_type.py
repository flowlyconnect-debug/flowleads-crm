"""Rename renovation type "Lämmitysjärjestelmän remontti" -> "Lämmitystaparemontti".

The heating type was split into Lämmitystaparemontti, Lämmityslaiteasennus and
Lämpöjohtoverkoston vianetsintä / tasapainotus, and Vesikalusteiden uusiminen /
korjaaminen was added (19.11.2026). Stored profiles, invites and listing
renovations keep working under the new name; the new types are added to older
listings from their text (app/listings/text_types.py::backfill_text_types).

Revision ID: a7c8d9e0f1b2
Revises: e2f3a4b5c6d7
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7c8d9e0f1b2"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None

OLD = "Lämmitysjärjestelmän remontti"
NEW = "Lämmitystaparemontti"

json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _rename(old: str, new: str) -> None:
    bind = op.get_bind()
    params = {"old": old, "new": new}
    bind.execute(sa.text("UPDATE listing_renovations SET remonttityyppi = :new WHERE remonttityyppi = :old"), params)
    bind.execute(sa.text("UPDATE search_profiles SET remonttityyppi = :new WHERE remonttityyppi = :old"), params)
    # profile names and invite type lists are "Putkiremontti, Lämmitys..." / "a; b" strings
    bind.execute(sa.text("UPDATE search_profiles SET name = REPLACE(name, :old, :new) WHERE name LIKE :pat"),
                 {**params, "pat": f"%{old}%"})
    bind.execute(
        sa.text("UPDATE customer_invites SET remonttityyppi = REPLACE(remonttityyppi, :old, :new) WHERE remonttityyppi LIKE :pat"),
        {**params, "pat": f"%{old}%"},
    )
    rows = bind.execute(sa.text("SELECT id, remonttityypit FROM search_profiles WHERE remonttityypit IS NOT NULL")).fetchall()
    for profile_id, raw in rows:
        types = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(types, list) or old not in types:
            continue
        renamed: list = []
        for t in types:
            t = new if t == old else t
            if t not in renamed:
                renamed.append(t)
        bind.execute(
            sa.text("UPDATE search_profiles SET remonttityypit = :types WHERE id = :id").bindparams(
                sa.bindparam("types", type_=json_type)
            ),
            {"types": renamed, "id": profile_id},
        )


def upgrade():
    _rename(OLD, NEW)


def downgrade():
    _rename(NEW, OLD)
