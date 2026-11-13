"""Leads from the Kohteet page no longer show where their listing came from:
drop the "Oikotie: <url>" / "Lähde: <url>" line from their notes and the
source tag ("oikotie" / "import") from their tags. New leads are created
without them (app/listings/services.py::_lead_payload).

Revision ID: f1a2b3c4d5e6
Revises: f0a1b2c3d4e5
"""

import json
import re

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None

_SOURCE_LINE = re.compile(r"^(Oikotie|Lähde): https?://\S+\s*$")
_SOURCE_TAGS = {"oikotie", "import"}


def _clean_notes(notes):
    if not notes:
        return notes
    lines = [line for line in notes.split("\n") if not _SOURCE_LINE.match(line.strip())]
    return "\n".join(lines)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return list(value) if isinstance(value, list) else []


def upgrade():
    bind = op.get_bind()
    leads = sa.table(
        "leads",
        sa.column("id", sa.Integer),
        sa.column("notes", sa.Text),
        sa.column("tags", sa.JSON),
    )
    rows = bind.execute(
        sa.text("SELECT id, notes, tags FROM leads WHERE source IN ('oikotie', 'import')")
    ).fetchall()
    for lead_id, notes, tags in rows:
        new_notes = _clean_notes(notes)
        old_tags = _as_list(tags)
        new_tags = [t for t in old_tags if str(t).strip().lower() not in _SOURCE_TAGS]
        if new_notes == notes and new_tags == old_tags:
            continue
        bind.execute(leads.update().where(leads.c.id == lead_id).values(notes=new_notes, tags=new_tags))


def downgrade():
    # The removed lines and tags were display only; the listing address is
    # still in the lead data (ai_contact_info). Nothing to restore.
    pass
