"""Ready-made "Taloyhtiöt" email sequence for organizations that have none.

New customers get it at onboarding (`app/sequences/defaults.py`); this gives it
to the existing ones so the Kohteet page can start a sequence on transfer.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
"""

import sqlalchemy as sa
from alembic import op

revision = "f0a1b2c3d4e5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade():
    from app.sequences.defaults import (
        DEFAULT_SEQUENCE_DESCRIPTION,
        DEFAULT_SEQUENCE_NAME,
        step_rows,
    )

    bind = op.get_bind()
    sequences = sa.table(
        "email_sequences",
        sa.column("id", sa.Integer),
        sa.column("organization_id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("is_active", sa.Boolean),
        sa.column("trigger_type", sa.String),
    )
    steps = sa.table(
        "email_sequence_steps",
        sa.column("sequence_id", sa.Integer),
        sa.column("order_index", sa.Integer),
        sa.column("delay_days", sa.Integer),
        sa.column("delay_hours", sa.Integer),
        sa.column("subject_template", sa.String),
        sa.column("body_html_template", sa.Text),
        sa.column("body_text_template", sa.Text),
        sa.column("condition", sa.JSON(none_as_null=True)),
    )
    org_ids = [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT o.id FROM organizations o WHERE NOT EXISTS "
                "(SELECT 1 FROM email_sequences s WHERE s.organization_id = o.id)"
            )
        )
    ]
    for org_id in org_ids:
        bind.execute(
            sequences.insert().values(
                organization_id=org_id,
                name=DEFAULT_SEQUENCE_NAME,
                description=DEFAULT_SEQUENCE_DESCRIPTION,
                is_active=True,
                trigger_type="manual",
            )
        )
        sequence_id = bind.execute(
            sa.text("SELECT id FROM email_sequences WHERE organization_id = :org AND name = :name"),
            {"org": org_id, "name": DEFAULT_SEQUENCE_NAME},
        ).scalar_one()
        bind.execute(steps.insert(), [{"sequence_id": sequence_id, **row} for row in step_rows()])


def downgrade():
    from app.sequences.defaults import DEFAULT_SEQUENCE_NAME

    bind = op.get_bind()
    # Only untouched copies with no enrollments are removed.
    ids = [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT s.id FROM email_sequences s WHERE s.name = :name AND NOT EXISTS "
                "(SELECT 1 FROM email_sequence_enrollments e WHERE e.sequence_id = s.id)"
            ),
            {"name": DEFAULT_SEQUENCE_NAME},
        )
    ]
    for sequence_id in ids:
        bind.execute(sa.text("DELETE FROM email_sequence_steps WHERE sequence_id = :id"), {"id": sequence_id})
        bind.execute(sa.text("DELETE FROM email_sequences WHERE id = :id"), {"id": sequence_id})
