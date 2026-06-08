from __future__ import annotations

from datetime import datetime, timezone

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.core.audit import log_audit
from app.core.permissions import require_role
from app.core.tenant import org_query_suffix, resolve_organization_id
from app.extensions import db
from app.search.constants import (
    FINNISH_REGIONS,
    REMONTTITYYPIT,
    SEARCH_JOB_STATUS_LABELS,
    SEARCH_SCHEDULE_LABELS,
)
from app.search.models import SEARCH_SCHEDULES
from app.search.profile_services import (
    SearchProfileServiceError,
    create_profile,
    create_test_search_job,
    delete_profile,
    get_latest_job,
    get_profile,
    list_profiles,
    update_profile,
)


def _relative_time_fi(value: datetime | None, now: datetime) -> str:
    if not value:
        return "Ei vielä"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    diff = now - value
    seconds = int(diff.total_seconds())
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min sitten"
    if seconds < 86400:
        return f"{seconds // 3600} h sitten"
    return f"{seconds // 86400} pv sitten"


def _profile_form_data() -> dict:
    regions = [r for r in request.form.getlist("regions") if r in FINNISH_REGIONS]
    schedule = (request.form.get("schedule_description") or "").strip()
    if schedule not in SEARCH_SCHEDULES:
        schedule = "daily"
    return {
        "name": (request.form.get("name") or "").strip(),
        "remonttityyppi": (request.form.get("remonttityyppi") or "").strip(),
        "regions": regions,
        "schedule_description": schedule,
        "crm_api_key": (request.form.get("crm_api_key") or "").strip() or None,
        "is_active": request.form.get("is_active") == "y",
    }


def _selected_profile_id(profiles: list) -> int | None:
    raw = request.args.get("id") or request.form.get("profile_id")
    if raw == "new":
        return None
    if raw in (None, ""):
        return profiles[0].id if profiles else None
    try:
        profile_id = int(raw)
    except (TypeError, ValueError):
        return profiles[0].id if profiles else None
    if any(profile.id == profile_id for profile in profiles):
        return profile_id
    return profiles[0].id if profiles else None


def register_search_profile_settings_routes(settings_bp):
    @settings_bp.route("/search-profiles", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin")
    def search_profiles_list():
        organization_id = resolve_organization_id()
        profiles = list_profiles(organization_id)
        selected_id = _selected_profile_id(profiles)
        selected_profile = None
        latest_job = None
        if selected_id is not None:
            selected_profile = get_profile(organization_id, selected_id)
            if selected_profile:
                latest_job = get_latest_job(selected_profile.id)

        now = datetime.now(timezone.utc)
        org_query = org_query_suffix(organization_id)
        return render_template(
            "settings/search_profiles.html",
            profiles=profiles,
            selected_profile=selected_profile,
            latest_job=latest_job,
            remonttityypit=REMONTTITYYPIT,
            finnish_regions=FINNISH_REGIONS,
            schedule_labels=SEARCH_SCHEDULE_LABELS,
            job_status_labels=SEARCH_JOB_STATUS_LABELS,
            org_query=org_query,
            now=now,
            relative_last_run=_relative_time_fi(
                selected_profile.last_run_at if selected_profile else None,
                now,
            ),
        )

    @settings_bp.route("/search-profiles", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    def search_profiles_create():
        organization_id = resolve_organization_id()
        org_query = org_query_suffix(organization_id)
        data = _profile_form_data()
        try:
            profile = create_profile(organization_id, **data)
            db.session.flush()
            log_audit(
                "search_profile_created",
                user_id=current_user.id,
                organization_id=organization_id,
                target_type="search_profile",
                target_id=profile.id,
                metadata={"name": profile.name},
            )
            db.session.commit()
            flash("Hakuprofiili luotu.", "success")
            return redirect(
                url_for(
                    "settings.search_profiles_list",
                    id=profile.id,
                    **org_query,
                )
            )
        except SearchProfileServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
            return redirect(url_for("settings.search_profiles_list", **org_query))

    @settings_bp.route("/search-profiles/<int:profile_id>", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    def search_profiles_update(profile_id: int):
        organization_id = resolve_organization_id()
        org_query = org_query_suffix(organization_id)
        profile = get_profile(organization_id, profile_id)
        if not profile:
            abort(404)
        data = _profile_form_data()
        try:
            update_profile(profile, **data)
            log_audit(
                "search_profile_updated",
                user_id=current_user.id,
                organization_id=organization_id,
                target_type="search_profile",
                target_id=profile.id,
                metadata={"name": profile.name, "is_active": profile.is_active},
            )
            db.session.commit()
            flash("Hakuprofiili tallennettu.", "success")
        except SearchProfileServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
        return redirect(
            url_for(
                "settings.search_profiles_list",
                id=profile_id,
                **org_query,
            )
        )

    @settings_bp.route(
        "/search-profiles/<int:profile_id>/create-test-job",
        methods=["POST"],
    )
    @login_required
    @require_role("admin", "superadmin")
    def search_profiles_create_test_job(profile_id: int):
        organization_id = resolve_organization_id()
        try:
            job = create_test_search_job(organization_id, profile_id)
            db.session.flush()
            log_audit(
                "search_test_job_created",
                user_id=current_user.id,
                organization_id=organization_id,
                target_type="search_job",
                target_id=job.id,
                metadata={"profile_id": profile_id},
            )
            db.session.commit()
            return jsonify(
                {
                    "success": True,
                    "job_id": job.id,
                    "profile_id": profile_id,
                    "status": "pending",
                }
            )
        except SearchProfileServiceError as exc:
            db.session.rollback()
            status = 404 if exc.code == "not_found" else 409 if exc.code == "job_exists" else 400
            return jsonify({"success": False, "error": exc.message, "code": exc.code}), status

    @settings_bp.route("/search-profiles/<int:profile_id>/delete", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    def search_profiles_delete(profile_id: int):
        organization_id = resolve_organization_id()
        org_query = org_query_suffix(organization_id)
        profile = get_profile(organization_id, profile_id)
        if not profile:
            abort(404)
        name = profile.name
        delete_profile(profile)
        log_audit(
            "search_profile_deleted",
            user_id=current_user.id,
            organization_id=organization_id,
            target_type="search_profile",
            target_id=profile_id,
            metadata={"name": name},
        )
        db.session.commit()
        flash("Hakuprofiili poistettu.", "success")
        return redirect(url_for("settings.search_profiles_list", **org_query))
