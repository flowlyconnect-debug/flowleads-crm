"""Add analytics reporting indexes

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-26 16:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def _index_names(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {idx["name"] for idx in insp.get_indexes(table)}


def _create_index(table: str, index_name: str, columns: list[str]) -> None:
    if index_name not in _index_names(table):
        op.create_index(index_name, table, columns, unique=False)


def upgrade():
    _create_index("leads", "ix_leads_status", ["status"])
    _create_index("leads", "ix_leads_source", ["source"])
    _create_index("leads", "ix_leads_score", ["score"])
    _create_index("activities", "ix_activities_user_id", ["user_id"])
    _create_index("activities", "ix_activities_type", ["type"])
    _create_index("activities", "ix_activities_created_at", ["created_at"])
    _create_index("email_logs", "ix_email_logs_user_id", ["user_id"])
    _create_index("email_logs", "ix_email_logs_status", ["status"])
    _create_index("email_logs", "ix_email_logs_sent_at", ["sent_at"])
    _create_index("pipeline_stages", "ix_pipeline_stages_order_index", ["order_index"])
    _create_index("api_keys", "ix_api_keys_last_used_at", ["last_used_at"])


def downgrade():
    for table, index_name in (
        ("api_keys", "ix_api_keys_last_used_at"),
        ("pipeline_stages", "ix_pipeline_stages_order_index"),
        ("email_logs", "ix_email_logs_sent_at"),
        ("email_logs", "ix_email_logs_status"),
        ("email_logs", "ix_email_logs_user_id"),
        ("activities", "ix_activities_created_at"),
        ("activities", "ix_activities_type"),
        ("activities", "ix_activities_user_id"),
        ("leads", "ix_leads_score"),
        ("leads", "ix_leads_source"),
        ("leads", "ix_leads_status"),
    ):
        if index_name in _index_names(table):
            op.drop_index(index_name, table_name=table)
