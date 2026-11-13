"""Task times typed in the UI were stored as if they were UTC: move them to
real UTC (they are Finnish time).

Only tasks a person created (created_by set) are converted: automatic tasks
(automations, lead rules, the API without a user) already used UTC.

Revision ID: f6f7a8b9c0d1
Revises: f5e6f7a8b9c0
"""

from datetime import timezone
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

revision = "f6f7a8b9c0d1"
down_revision = "f5e6f7a8b9c0"
branch_labels = None
depends_on = None

HELSINKI = ZoneInfo("Europe/Helsinki")


def _shift(value, *, to_utc: bool):
    if value is None:
        return None
    naive = value.replace(tzinfo=None) if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    if to_utc:  # wall-clock Finnish time stored as UTC -> real UTC
        return naive.replace(tzinfo=HELSINKI).astimezone(timezone.utc)
    return naive.replace(tzinfo=timezone.utc).astimezone(HELSINKI).replace(tzinfo=None).replace(tzinfo=timezone.utc)


def _convert(to_utc: bool):
    bind = op.get_bind()
    tasks = sa.table(
        "tasks",
        sa.column("id", sa.Integer),
        sa.column("created_by", sa.Integer),
        sa.column("due_date", sa.DateTime(timezone=True)),
        sa.column("reminder_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(sa.select(tasks.c.id, tasks.c.due_date, tasks.c.reminder_at).where(tasks.c.created_by.isnot(None))).all()
    for task_id, due, reminder in rows:
        bind.execute(
            tasks.update()
            .where(tasks.c.id == task_id)
            .values(due_date=_shift(due, to_utc=to_utc), reminder_at=_shift(reminder, to_utc=to_utc))
        )


def upgrade():
    _convert(to_utc=True)


def downgrade():
    _convert(to_utc=False)
