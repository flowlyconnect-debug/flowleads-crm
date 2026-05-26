from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

ENTITY_TYPES = ("lead", "contact", "company")
FIELD_TYPES = ("text", "number", "date", "boolean", "select", "multiselect", "url")

JsonColumn = JSON().with_variant(JSONB(), "postgresql")


class CustomFieldDefinition(db.Model):
    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "entity_type",
            "name",
            name="uq_custom_field_def_org_entity_name",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, default="lead")
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    field_type: Mapped[str] = mapped_column(String(32), nullable=False)
    options: Mapped[dict | list | None] = mapped_column(JsonColumn, nullable=True)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_searchable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship("Organization")  # noqa: F821
    values: Mapped[list["CustomFieldValue"]] = relationship(
        "CustomFieldValue", back_populates="definition"
    )


class CustomFieldValue(db.Model):
    __tablename__ = "custom_field_values"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "entity_type",
            "entity_id",
            "field_definition_id",
            name="uq_custom_field_value_entity_field",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, default="lead")
    field_definition_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("custom_field_definitions.id"), nullable=False, index=True
    )

    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_number: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    value_boolean: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    value_json: Mapped[dict | list | None] = mapped_column(JsonColumn, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    definition: Mapped[CustomFieldDefinition] = relationship(
        "CustomFieldDefinition", back_populates="values"
    )
