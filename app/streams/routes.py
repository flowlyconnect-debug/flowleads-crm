from __future__ import annotations

from datetime import datetime, timezone

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.core.audit import log_audit
from app.core.permissions import require_role
from app.extensions import db
from app.api.models import APIKey
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


def _format_user_label(user: User) -> str:
    local_part = (user.email or "").split("@")[0].strip()
    parts = [p for p in local_part.replace(".", " ").replace("_", " ").split(" ") if p]
    if not parts:
        return user.email
    return " ".join(part.capitalize() for part in parts)


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
                next_priority = (
                    db.session.query(db.func.max(LeadStream.priority))
                    .filter_by(organization_id=organization_id)
                    .scalar()
                    or 0
                ) + 1
                stream = LeadStream(organization_id=organization_id, **data)
                stream.priority = next_priority
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
        api_key = (
            APIKey.query.filter_by(organization_id=organization_id, is_active=True)
            .order_by(APIKey.created_at.asc())
            .first()
        )
        user_display_map = {user.id: _format_user_label(user) for user in users}
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
            org_api_key=api_key,
            user_display_map=user_display_map,
        )

    @settings_bp.route("/streams/reorder", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    def reorder_streams():
        organization_id = _scope_org_id()
        payload = request.get_json(silent=True) or {}
        order = payload.get("order")
        if not isinstance(order, list):
            return jsonify({"success": False, "error": "Invalid order payload"}), 400

        ids = []
        for item in order:
            try:
                ids.append(int(item))
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "Invalid stream id"}), 400
        if not ids:
            return jsonify({"success": True})

        streams = LeadStream.query.filter(
            LeadStream.organization_id == organization_id,
            LeadStream.id.in_(ids),
        ).all()
        if len(streams) != len(set(ids)):
            return jsonify({"success": False, "error": "Stream not found"}), 404

        stream_by_id = {stream.id: stream for stream in streams}
        for index, stream_id in enumerate(ids, start=1):
            stream_by_id[stream_id].priority = index

        log_audit(
            "stream_updated",
            user_id=current_user.id,
            organization_id=organization_id,
            target_type="lead_stream",
            metadata={"action": "reorder", "order": ids},
        )
        db.session.commit()
        return jsonify({"success": True})
