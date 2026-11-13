"""Database backup without pg_dump: every table of the app's schema as
gzipped JSON lines, written and read with SQLAlchemy.

Why: pg_dump refuses to dump a server newer than itself, and the managed
Python runtime does not let us pick the PostgreSQL client version (production
runs Postgres 18). `create_backup` uses this when pg_dump is missing or too
old (BACKUP_DB_FORMAT=auto) or always (BACKUP_DB_FORMAT=python).

File: first line {"format": "flowleads-logical", "version": 1,
"alembic_revision": ..., "tables": [...]}, then one {"t": table, "r": row}
per row. Values that JSON cannot hold are tagged ($dt, $d, $dec, $b64) and
JSON columns are wrapped ($json) so their content is never mistaken for a tag.

Restore replaces the content of every table in one transaction and only into
a database at the same schema revision.
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa

logger = logging.getLogger(__name__)

FORMAT = "flowleads-logical"
VERSION = 1
BATCH = 500


class LogicalBackupError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _encode(value, column_type):
    if value is None:
        return None
    if isinstance(column_type, sa.JSON):
        return {"$json": value}
    if isinstance(value, datetime):
        return {"$dt": value.isoformat()}
    if isinstance(value, date):
        return {"$d": value.isoformat()}
    if isinstance(value, time):
        return {"$t": value.isoformat()}
    if isinstance(value, Decimal):
        return {"$dec": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$b64": base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode(value):
    if isinstance(value, dict) and len(value) == 1:
        (tag, inner), = value.items()
        if tag == "$json":
            return inner
        if tag == "$dt":
            return datetime.fromisoformat(inner)
        if tag == "$d":
            return date.fromisoformat(inner)
        if tag == "$t":
            return time.fromisoformat(inner)
        if tag == "$dec":
            return Decimal(inner)
        if tag == "$b64":
            return base64.b64decode(inner)
    return value


def _revision(connection) -> str | None:
    try:
        return connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    except Exception:
        return None


def _tables(metadata, connection) -> list[sa.Table]:
    existing = set(sa.inspect(connection).get_table_names())
    return [table for table in metadata.sorted_tables if table.name in existing]


def dump_database(engine, metadata, dest: Path) -> dict:
    """Write every table of `metadata` that exists in the database. Returns
    {"tables": n, "rows": n, "alembic_revision": ...}."""
    rows_total = 0
    with engine.connect() as connection, gzip.open(dest, "wt", encoding="utf-8") as out:
        tables = _tables(metadata, connection)
        revision = _revision(connection)
        out.write(json.dumps({"format": FORMAT, "version": VERSION, "alembic_revision": revision, "tables": [t.name for t in tables]}) + "\n")
        for table in tables:
            types = {column.name: column.type for column in table.columns}
            order = [c for c in table.primary_key.columns] or list(table.columns)[:1]
            result = connection.execution_options(stream_results=True).execute(sa.select(table).order_by(*order))
            for row in result.mappings():
                out.write(json.dumps({"t": table.name, "r": {k: _encode(v, types[k]) for k, v in row.items()}}, ensure_ascii=False) + "\n")
                rows_total += 1
    return {"tables": len(tables), "rows": rows_total, "alembic_revision": revision}


def read_header(src: Path) -> dict:
    with gzip.open(src, "rt", encoding="utf-8") as handle:
        header = json.loads(handle.readline() or "{}")
    if header.get("format") != FORMAT:
        raise LogicalBackupError("Not a FlowLeads database backup.")
    return header


def restore_database(engine, metadata, src: Path) -> dict:
    """Replace the content of every table with the backup's rows (one
    transaction). Refuses a backup made at another schema revision."""
    header = read_header(src)
    with engine.begin() as connection:
        current = _revision(connection)
        if header.get("alembic_revision") != current:
            raise LogicalBackupError(
                f"Backup schema {header.get('alembic_revision')} differs from database schema {current}."
            )
        tables = {table.name: table for table in _tables(metadata, connection)}
        wanted = [name for name in header.get("tables", []) if name in tables]
        for name in reversed([t.name for t in metadata.sorted_tables if t.name in tables]):
            connection.execute(tables[name].delete())
        counts: dict[str, int] = {}
        batch: list[dict] = []
        batch_table: str | None = None

        def flush():
            if batch:
                connection.execute(tables[batch_table].insert(), batch)
                counts[batch_table] = counts.get(batch_table, 0) + len(batch)
                batch.clear()

        with gzip.open(src, "rt", encoding="utf-8") as handle:
            handle.readline()
            for line in handle:
                item = json.loads(line)
                name = item.get("t")
                if name not in tables or name not in wanted:
                    continue
                if name != batch_table:
                    flush()
                    batch_table = name
                columns = {c.name for c in tables[name].columns}
                batch.append({k: _decode(v) for k, v in item["r"].items() if k in columns})
                if len(batch) >= BATCH:
                    flush()
            flush()
        if connection.dialect.name == "postgresql":
            for name in tables:
                table = tables[name]
                if "id" in table.c and isinstance(table.c.id.type, sa.Integer):
                    connection.execute(
                        sa.text(
                            f"SELECT setval(pg_get_serial_sequence(:t, 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM \"{name}\""
                        ),
                        {"t": name},
                    )
    return {"tables": len(counts), "rows": sum(counts.values()), "counts": counts}
