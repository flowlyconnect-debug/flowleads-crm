from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

ROLES = ("superadmin", "admin", "user", "api_client")
UI_LOGIN_ROLES = ("superadmin", "admin", "user")


class Organization(db.Model):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_from_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email_from_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mailgun_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Set only once Mailgun confirms the domain's SPF/DKIM DNS records are
    # valid — cleared whenever mailgun_domain changes. Sending only uses this
    # domain's envelope once it's set (see _resolve_sending_domain); until
    # then, sales email still goes out (From-header shows the org's address)
    # but via the shared FlowLeads domain.
    mailgun_domain_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # TODO: per-org Mailgun API key — store as mailgun_api_key_encrypted once encryption exists.
    # Optional modules the org's admin opted into during invite onboarding (see
    # app/admin/constants.py FEATURE_CATALOG). Not yet wired into feature gating
    # elsewhere in the app — just captured for now.
    enabled_features: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    users: Mapped[list["User"]] = relationship("User", back_populates="organization")

    def __repr__(self):
        return f"<Organization {self.slug}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    organization: Mapped[Organization | None] = relationship("Organization", back_populates="users")
    backup_codes: Mapped[list["BackupCode"]] = relationship(  # noqa: F821
        "BackupCode", back_populates="user", cascade="all, delete-orphan"
    )

    def is_superadmin(self) -> bool:
        return self.role == "superadmin"

    def can_login_ui(self) -> bool:
        return self.is_active and self.role in UI_LOGIN_ROLES

    def __repr__(self):
        return f"<User {self.email}>"


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    user: Mapped[User | None] = relationship("User", foreign_keys=[user_id])
