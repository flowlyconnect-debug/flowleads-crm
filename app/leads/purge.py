"""Hard-delete every lead (all organizations), and optionally the whole listing
catalogue. Operator tool behind `flask purge-leads`; the UI only archives.

Rows that cannot exist without a lead (activities, emails, proposals, sequence
enrollments, tasks, ...) are deleted with it. Rows that merely point at a lead
(calendar events, form submissions, automation logs, export requests) are kept
with the reference cleared.
"""

from __future__ import annotations

from sqlalchemy import Table, and_, delete, func, not_, or_, select, true, update

from app.extensions import db

# Nullable references whose rows are still meaningless without the lead.
_DELETE_WITH_PARENT = {("tasks", "lead_id"), ("listing_matches", "lead_id")}


def _count(table: Table, condition) -> int:
    return db.session.execute(select(func.count()).select_from(table).where(condition)).scalar() or 0


def _count_new(table: Table, condition, key: str, seen: dict, dry_run: bool) -> int:
    """Rows matching `condition` not counted before under `key`. A real run
    has deleted the earlier rows already; a dry run must not count a row that
    two paths reach (a listing match is both a lead's and a listing's child)
    twice."""
    previous = seen.setdefault(key, [])
    effective = and_(condition, not_(or_(*previous))) if (dry_run and previous) else condition
    previous.append(condition)
    return _count(table, effective)


def _purge_table(
    table: Table,
    condition,
    counts: dict,
    dry_run: bool,
    stack: tuple = (),
    delete_with_parent: frozenset | set = frozenset(),
    seen: dict | None = None,
) -> None:
    """Delete `table` rows matching `condition`, children first. A nullable
    reference keeps its row with the reference cleared, unless it is listed in
    _DELETE_WITH_PARENT or `delete_with_parent`."""
    seen = {} if seen is None else seen
    always_delete = _DELETE_WITH_PARENT | set(delete_with_parent)
    if "id" in table.c:
        parent_ids = select(table.c.id).where(condition)
        for child in db.metadata.tables.values():
            for fk in child.foreign_keys:
                if fk.column.table is not table or fk.column.name != "id":
                    continue
                column = fk.parent
                child_condition = column.in_(parent_ids)
                keep_row = column.nullable and (child.name, column.name) not in always_delete
                if keep_row or child is table or child.name in stack:
                    key = f"{child.name}.{column.name} (viittaus tyhjennetty)"
                    counts[key] = counts.get(key, 0) + _count_new(child, child_condition, key, seen, dry_run)
                    if not dry_run:
                        db.session.execute(
                            update(child).where(child_condition).values({column.name: None}),
                            execution_options={"synchronize_session": False},
                        )
                else:
                    _purge_table(
                        child, child_condition, counts, dry_run, stack + (table.name,), delete_with_parent, seen
                    )

    counts[table.name] = counts.get(table.name, 0) + _count_new(table, condition, table.name, seen, dry_run)
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

    seen: dict = {}
    _purge_table(tables["leads"], true(), counts, dry_run, seen=seen)

    if with_listings:
        _purge_table(tables["listings"], true(), counts, dry_run, seen=seen)

    if not dry_run:
        # Running totals shown in the UI would otherwise still count the deleted leads.
        db.session.execute(update(tables["lead_streams"]).values(lead_count=0, last_lead_at=None))
        db.session.execute(update(tables["org_lead_settings"]).values(total_lead_count=0, last_lead_at=None))
        db.session.expire_all()

    return {name: n for name, n in counts.items() if n}
