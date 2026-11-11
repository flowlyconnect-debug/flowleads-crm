"""Hard-delete every lead (all organizations), and optionally the whole listing
catalogue. Operator tool behind `flask purge-leads`; the UI only archives.
`flask delete-organizations` uses the same walk to remove whole customers.

Rows that cannot exist without a lead (activities, emails, proposals, sequence
enrollments, tasks, ...) are deleted with it. Rows that merely point at a lead
(calendar events, form submissions, automation logs, export requests) are kept
with the reference cleared.
"""

from __future__ import annotations

from sqlalchemy import Table, delete, func, select, true, update

from app.extensions import db

# Nullable references whose rows are still meaningless without the lead.
_DELETE_WITH_PARENT = {("tasks", "lead_id"), ("listing_matches", "lead_id")}


def _count(table: Table, condition) -> int:
    return db.session.execute(select(func.count()).select_from(table).where(condition)).scalar() or 0


def _purge_table(
    table: Table,
    condition,
    counts: dict,
    dry_run: bool,
    stack: tuple = (),
    delete_columns: frozenset = frozenset(),
) -> None:
    """Delete `table` rows matching `condition`, children first.

    A nullable reference is normally cleared and its row kept; references whose
    column name is in `delete_columns` take their rows with them instead.
    """
    if "id" in table.c:
        parent_ids = select(table.c.id).where(condition)
        for child in db.metadata.tables.values():
            for fk in child.foreign_keys:
                if fk.column.table is not table or fk.column.name != "id":
                    continue
                column = fk.parent
                child_condition = column.in_(parent_ids)
                keep_row = (
                    column.nullable
                    and (child.name, column.name) not in _DELETE_WITH_PARENT
                    and column.name not in delete_columns
                )
                if keep_row or child is table or child.name in stack:
                    key = f"{child.name}.{column.name} (viittaus tyhjennetty)"
                    counts[key] = counts.get(key, 0) + _count(child, child_condition)
                    if not dry_run:
                        db.session.execute(
                            update(child).where(child_condition).values({column.name: None}),
                            execution_options={"synchronize_session": False},
                        )
                else:
                    _purge_table(child, child_condition, counts, dry_run, stack + (table.name,), delete_columns)

    counts[table.name] = counts.get(table.name, 0) + _count(table, condition)
    if not dry_run:
        db.session.execute(delete(table).where(condition), execution_options={"synchronize_session": False})


def purge_all_leads(*, with_listings: bool = False, dry_run: bool = True) -> dict:
    """Returns {table: affected rows}. Does not commit; with dry_run nothing changes."""
    tables = db.metadata.tables
    counts: dict[str, int] = {}

    custom_values = tables["custom_field_values"]
    lead_values = custom_values.c.entity_type == "lead"
    counts["custom_field_values"] = _count(custom_values, lead_values)
    if not dry_run:
        db.session.execute(delete(custom_values).where(lead_values), execution_options={"synchronize_session": False})

    _purge_table(tables["leads"], true(), counts, dry_run)

    if with_listings:
        _purge_table(tables["listings"], true(), counts, dry_run)

    if not dry_run:
        # Running totals shown in the UI would otherwise still count the deleted leads.
        db.session.execute(update(tables["lead_streams"]).values(lead_count=0, last_lead_at=None))
        db.session.execute(update(tables["org_lead_settings"]).values(total_lead_count=0, last_lead_at=None))
        db.session.expire_all()

    return {name: n for name, n in counts.items() if n}


def delete_organizations(organization_ids: list[int]) -> dict:
    """Hard-delete whole organizations: users, profiles, leads, settings, audit
    rows, everything carrying their organization_id. The shared listing
    catalogue stays; only the organizations' own matches go.

    Returns {table: deleted rows}. Does not commit: the caller rolls back for a
    dry run, which is what makes the counts exact.
    """
    tables = db.metadata.tables
    counts: dict[str, int] = {}
    organizations = tables["organizations"]
    _purge_table(
        organizations,
        organizations.c.id.in_(organization_ids),
        counts,
        dry_run=False,
        delete_columns=frozenset({"organization_id"}),
    )
    db.session.expire_all()
    return {name: n for name, n in counts.items() if n}
