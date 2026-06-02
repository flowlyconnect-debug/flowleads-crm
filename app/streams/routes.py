from __future__ import annotations

from datetime import datetime, timezone

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.core.audit import log_audit
from app.core.permissions import require_role
from app.extensions import db
from app.leads.models import PipelineStage
from app.streams.services import LeadRoutingService
from app.users.models import User


def _format_user_label(user: User) -> str:
    local_part = (user.email or "").split("@")[0].strip()
    parts = [p for p in local_part.replace(".", " ").replace("_", " ").split(" ") if p]
    if not parts:
        return user.email
    return " ".join(part.capitalize() for part in parts)


def _relative_time_fi(value, now: datetime) -> str:
    if not value:
        return "Ei vielä"
    diff = now - value
    seconds = int(diff.total_seconds())
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min sitten"
    if seconds < 86400:
        return f"{seconds // 3600}h sitten"
    return f"{seconds // 86400} pv sitten"


def register_stream_settings_routes(settings_bp):
    @settings_bp.route("/leads", methods=["GET", "POST"])
    @login_required
    @require_role("admin", "superadmin")
    def lead_settings():
        organization_id = current_user.organization_id
        if organization_id is None:
            abort(403)

        if request.method == "POST":
            settings = LeadRoutingService.get_settings(organization_id)
            stage_raw = request.form.get("default_pipeline_stage_id")
            owner_raw = request.form.get("default_owner_id")
            tags_raw = request.form.get("default_tags") or ""
            default_industry = (request.form.get("default_industry") or "").strip() or None
            default_region = (request.form.get("default_region") or "").strip() or None

            try:
                stage_id = int(stage_raw) if stage_raw else None
                owner_id = int(owner_raw) if owner_raw else None
            except (TypeError, ValueError):
                flash("Virheellinen arvo.", "danger")
                return redirect(url_for("settings.lead_settings"))

            tags = [item.strip() for item in tags_raw.split(",") if item.strip()]

            if stage_id:
                stage = PipelineStage.query.filter_by(
                    id=stage_id,
                    organization_id=organization_id,
                ).first()
                if not stage:
                    flash("Virheellinen vaihe.", "danger")
                    return redirect(url_for("settings.lead_settings"))

            if owner_id:
                owner = User.query.filter_by(
                    id=owner_id,
                    organization_id=organization_id,
                    is_active=True,
                ).first()
                if not owner:
                    flash("Virheellinen omistaja.", "danger")
                    return redirect(url_for("settings.lead_settings"))

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
            flash("Asetukset tallennettu", "success")
            return redirect(url_for("settings.lead_settings"))

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
        user_display_map = {user.id: _format_user_label(user) for user in users}
        now = datetime.now(timezone.utc)
        return render_template(
            "settings/lead_settings.html",
            settings=settings,
            stages=stages,
            users=users,
            user_display_map=user_display_map,
            relative_last_lead_at=_relative_time_fi(settings.last_lead_at, now),
        )

    @settings_bp.route("/streams", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin")
    def streams_index():
        return redirect(url_for("settings.lead_settings"), code=302)
