from __future__ import annotations

from datetime import datetime, timezone

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.core.audit import log_audit
from app.core.permissions import require_role
from app.extensions import db
from app.leads.models import LeadStream, PipelineStage
from app.streams.services import StreamHealthService
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


def register_stream_settings_routes(settings_bp):
    @settings_bp.route("/streams", methods=["GET", "POST"])
    @login_required
    @require_role("admin", "superadmin")
    def streams_index():
        organization_id = _scope_org_id()
        org_query = _org_query()
        migration_needed = False
        if request.method == "POST":
            action = request.form.get("action", "create")
            stream_id = request.form.get("stream_id")
            stream = None
            if stream_id:
                stream = LeadStream.query.filter_by(
                    id=int(stream_id), organization_id=organization_id
                ).first_or_404()

            if action == "delete" and stream:
                log_audit(
                    "stream_deleted",
                    user_id=current_user.id,
                    organization_id=organization_id,
                    target_type="lead_stream",
                    target_id=stream.id,
                )
                db.session.delete(stream)
                db.session.commit()
                flash("Liidivirta poistettu.", "success")
                return redirect(url_for("settings.streams_index", **org_query))

            if action == "toggle" and stream:
                stream.is_active = not stream.is_active
                log_audit(
                    "stream_toggled",
                    user_id=current_user.id,
                    organization_id=organization_id,
                    target_type="lead_stream",
                    target_id=stream.id,
                    metadata={"is_active": stream.is_active},
                )
                db.session.commit()
                flash("Liidivirran tila paivitetty.", "success")
                return redirect(url_for("settings.streams_index", **org_query))

            default_tags = [
                item.strip()
                for item in (request.form.get("default_tags") or "").split(",")
                if item.strip()
            ]
            data = {
                "name": (request.form.get("name") or "").strip(),
                "source_match": (request.form.get("source_match") or "").strip() or None,
                "segment_key": (request.form.get("segment_key") or "").strip() or None,
                "priority": int(request.form.get("priority") or 10),
                "pipeline_stage_id": int(request.form["pipeline_stage_id"])
                if request.form.get("pipeline_stage_id")
                else None,
                "owner_id": int(request.form["owner_id"]) if request.form.get("owner_id") else None,
                "default_tags": default_tags,
                "is_active": request.form.get("is_active") == "on",
            }
            if not data["name"]:
                flash("Nimi on pakollinen.", "danger")
                return redirect(url_for("settings.streams_index", **org_query))

            if stream:
                for key, value in data.items():
                    setattr(stream, key, value)
                action_name = "stream_updated"
                target_id = stream.id
                flash("Liidivirta paivitetty.", "success")
            else:
                stream = LeadStream(organization_id=organization_id, **data)
                db.session.add(stream)
                db.session.flush()
                action_name = "stream_created"
                target_id = stream.id
                flash("Liidivirta luotu.", "success")
            log_audit(
                action_name,
                user_id=current_user.id,
                organization_id=organization_id,
                target_type="lead_stream",
                target_id=target_id,
            )
            db.session.commit()
            return redirect(url_for("settings.streams_index", **org_query))

        try:
            streams = (
                LeadStream.query.filter_by(organization_id=organization_id)
                .order_by(LeadStream.priority.asc(), LeadStream.id.asc())
                .all()
            )
        except SQLAlchemyError:
            db.session.rollback()
            streams = []
            migration_needed = True
            flash(
                "Liidivirrat ei ole kaytettavissa viela. Suorita tietokantamigraatio (flask db upgrade).",
                "warning",
            )
        editing_stream = None
        edit_id = request.args.get("edit")
        if edit_id:
            try:
                editing_stream = LeadStream.query.filter_by(
                    id=int(edit_id), organization_id=organization_id
                ).first()
            except (ValueError, SQLAlchemyError):
                db.session.rollback()
                editing_stream = None
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
        try:
            stale_ids = {s.id for s in StreamHealthService.get_stale_streams(organization_id)}
        except SQLAlchemyError:
            db.session.rollback()
            stale_ids = set()
            migration_needed = True
        return render_template(
            "streams/index.html",
            streams=streams,
            stages=stages,
            users=users,
            stale_ids=stale_ids,
            stale_days=StreamHealthService.STALE_DAYS,
            now=datetime.now(timezone.utc),
            org_query=org_query,
            editing_stream=editing_stream,
            migration_needed=migration_needed,
        )
