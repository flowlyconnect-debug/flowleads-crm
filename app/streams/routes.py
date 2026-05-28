from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.core.audit import log_audit
from app.core.permissions import require_role
from app.extensions import db
from app.api.models import APIKey
from app.leads.models import PipelineStage
from app.streams.services import LeadHealthService, LeadRoutingService
from app.users.models import User


def _scope_org_id() -> int:
    if current_user.is_superadmin():
        org_id_raw = request.args.get("organization_id")
        if org_id_raw:
            try:
                return int(org_id_raw)
            except (TypeError, ValueError):
                return current_user.organization_id
    return current_user.organization_id


def _org_query() -> dict:
    if current_user.is_superadmin() and request.args.get("organization_id"):
        return {"organization_id": request.args.get("organization_id")}
    return {}


def _format_user_label(user: User) -> str:
    local_part = (user.email or "").split("@")[0].strip()
    parts = [p for p in local_part.replace(".", " ").replace("_", " ").split(" ") if p]
    if not parts:
        return user.email
    return " ".join(part.capitalize() for part in parts)


def _relative_time_fi(value, now: datetime) -> str:
    if not value:
        return "Ei viela"
    diff = now - value
    seconds = int(diff.total_seconds())
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min sitten"
    if seconds < 86400:
        return f"{seconds // 3600}h sitten"
    return f"{seconds // 86400} pv sitten"


def register_stream_settings_routes(settings_bp):
    @settings_bp.route("/leads", methods=["GET", "PUT"])
    @login_required
    @require_role("admin", "superadmin")
    def lead_settings():
        organization_id = _scope_org_id()
        org_query = _org_query()
        if request.method == "PUT":
            settings = LeadRoutingService.get_settings(organization_id)
            payload = request.get_json(silent=True) or {}
            stage_raw = payload.get("default_pipeline_stage_id")
            owner_raw = payload.get("default_owner_id")
            tags_raw = payload.get("default_tags") or ""
            default_industry = (payload.get("default_industry") or "").strip() or None
            default_region = (payload.get("default_region") or "").strip() or None

            try:
                stage_id = int(stage_raw) if stage_raw not in (None, "", "null") else None
                owner_id = int(owner_raw) if owner_raw not in (None, "", "null") else None
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "Virheellinen arvo."}), 400
            tags = [item.strip() for item in tags_raw.split(",") if item.strip()]

            if stage_id:
                stage = PipelineStage.query.filter_by(
                    id=stage_id,
                    organization_id=organization_id,
                ).first()
                if not stage:
                    return jsonify({"success": False, "error": "Virheellinen vaihe."}), 400

            if owner_id:
                owner = User.query.filter_by(
                    id=owner_id,
                    organization_id=organization_id,
                    is_active=True,
                ).first()
                if not owner:
                    return jsonify({"success": False, "error": "Virheellinen omistaja."}), 400

            settings.default_pipeline_stage_id = stage_id
            settings.default_owner_id = owner_id
            settings.default_tags = tags
            settings.default_industry = default_industry
            settings.default_region = default_region
            log_audit(
                "lead_settings_updated",
                user_id=current_user.id,
                organization_id=organization_id,
                target_type="org_lead_settings",
                target_id=settings.id,
                metadata={
                    "default_pipeline_stage_id": stage_id,
                    "default_owner_id": owner_id,
                    "default_tags": tags,
                    "default_industry": default_industry,
                    "default_region": default_region,
                },
            )
            db.session.commit()
            return jsonify({"success": True})

        settings = LeadRoutingService.get_settings(organization_id)
        stages = (
            PipelineStage.query.filter_by(organization_id=organization_id)
            .order_by(PipelineStage.order_index.asc())
            .all()
        )
        users = (
            User.query.filter_by(organization_id=organization_id, is_active=True)
            .order_by(User.email.asc())
            .all()
        )
        api_keys = (
            APIKey.query.filter_by(organization_id=organization_id, is_active=True)
            .order_by(APIKey.created_at.desc())
            .all()
        )
        user_display_map = {user.id: _format_user_label(user) for user in users}
        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(days=LeadHealthService.STALE_DAYS)
        is_stale = bool(
            settings.last_lead_at
            and settings.total_lead_count > 0
            and settings.last_lead_at < stale_cutoff
        )
        return render_template(
            "settings/lead_settings.html",
            settings=settings,
            stages=stages,
            users=users,
            user_display_map=user_display_map,
            api_keys=api_keys,
            org_query=org_query,
            now=now,
            stale_days=LeadHealthService.STALE_DAYS,
            is_stale=is_stale,
            relative_last_lead_at=_relative_time_fi(settings.last_lead_at, now),
        )

    @settings_bp.route("/streams", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin")
    def streams_index():
        return redirect(url_for("settings.lead_settings", **_org_query()), code=302)
