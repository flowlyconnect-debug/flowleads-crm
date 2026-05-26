"""Calendar views and lead meeting routes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.calendar.forms import ScheduleMeetingForm
from app.calendar.services import CalendarService, CalendarServiceError
from app.core.permissions import require_2fa, require_role
from app.extensions import db
from app.leads.permissions import resolve_organization_id
from app.leads.services import LeadServiceError, get_lead_for_org

calendar_bp = Blueprint("calendar", __name__)

UI_ROLES = ("superadmin", "admin", "user")


def register_calendar_lead_routes(leads_bp):
    @leads_bp.route("/<int:lead_id>/meetings", methods=["GET"])
    @login_required
    @require_role(*UI_ROLES)
    def lead_meetings(lead_id: int):
        organization_id = resolve_organization_id()
        try:
            get_lead_for_org(lead_id, organization_id)
        except LeadServiceError:
            abort(404)
        meetings = CalendarService.get_events_for_lead(lead_id, organization_id)
        return jsonify(
            {
                "success": True,
                "upcoming": [_event_json(e) for e in meetings["upcoming"]],
                "past": [_event_json(e) for e in meetings["past"]],
            }
        )

    @leads_bp.route("/<int:lead_id>/meetings/schedule", methods=["POST"])
    @login_required
    @require_role(*UI_ROLES)
    def schedule_lead_meeting(lead_id: int):
        organization_id = resolve_organization_id()
        try:
            lead = get_lead_for_org(lead_id, organization_id)
        except LeadServiceError:
            abort(404)

        if request.is_json:
            data = request.get_json(silent=True) or {}
            title = (data.get("title") or "").strip()
            start_raw = data.get("start_at")
            duration = int(data.get("duration_minutes") or 30)
            description = data.get("description")
            attendees_raw = data.get("attendees") or []
            if isinstance(attendees_raw, str):
                attendees = [a.strip() for a in attendees_raw.split(",") if a.strip()]
            else:
                attendees = list(attendees_raw)
            video_meeting = bool(data.get("video_meeting", True))
            location = data.get("location")
        else:
            form = ScheduleMeetingForm()
            if not form.validate_on_submit():
                flash("Tarkista lomakkeen tiedot.", "danger")
                return redirect(
                    url_for(
                        "leads.detail",
                        lead_id=lead_id,
                        organization_id=organization_id
                        if current_user.is_superadmin()
                        else None,
                    )
                )
            title = form.title.data
            start_raw = form.start_at.data
            duration = form.duration_minutes.data
            description = form.description.data
            attendees = [
                a.strip() for a in (form.attendees.data or "").split(",") if a.strip()
            ]
            video_meeting = form.video_meeting.data
            location = form.location.data

        if not title:
            title = f"Tapaaminen — {lead.company or lead.display_name}"

        if isinstance(start_raw, str):
            start_at = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
        else:
            start_at = start_raw
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=timezone.utc)
        end_at = start_at + timedelta(minutes=duration)

        if not attendees and lead.email:
            attendees = [lead.email]

        try:
            event = CalendarService.create_event(
                current_user.id,
                lead_id,
                title,
                start_at,
                end_at,
                description=description,
                attendees=attendees,
                video_meeting=video_meeting,
                location=location,
                organization_id=organization_id,
            )
            db.session.commit()
        except CalendarServiceError as exc:
            db.session.rollback()
            if request.is_json:
                return jsonify({"success": False, "error": exc.code, "message": exc.message}), 400
            flash(exc.message, "danger")
            return redirect(
                url_for(
                    "leads.detail",
                    lead_id=lead_id,
                    organization_id=organization_id if current_user.is_superadmin() else None,
                )
            )

        if request.is_json:
            return jsonify({"success": True, "event": _event_json(event)})
        flash("Tapaaminen aikataulutettu.", "success")
        return redirect(
            url_for(
                "leads.detail",
                lead_id=lead_id,
                organization_id=organization_id if current_user.is_superadmin() else None,
            )
        )


def _event_json(event) -> dict:
    return {
        "id": event.id,
        "title": event.title,
        "start_at": event.start_at.isoformat() if event.start_at else None,
        "end_at": event.end_at.isoformat() if event.end_at else None,
        "attendees": event.attendees or [],
        "meeting_url": event.meeting_url,
        "location": event.location,
        "status": event.status,
        "provider": event.provider,
    }


@calendar_bp.before_request
@login_required
def calendar_auth():
    if current_user.role not in UI_ROLES:
        abort(403)


@calendar_bp.route("/calendar")
@require_role(*UI_ROLES)
@require_2fa
def my_calendar():
    organization_id = resolve_organization_id()
    events = CalendarService.get_week_events(current_user.id, organization_id)
    return render_template(
        "calendar/week.html",
        today_events=events["today"],
        week_events=events["week"],
        organization_id=organization_id,
    )


@calendar_bp.route("/calendar/events/<int:event_id>", methods=["DELETE", "POST"])
@require_role(*UI_ROLES)
def cancel_calendar_event(event_id: int):
    organization_id = resolve_organization_id()
    try:
        event = CalendarService.cancel_event(event_id, current_user.id, organization_id)
        db.session.commit()
    except CalendarServiceError as exc:
        db.session.rollback()
        if request.is_json or request.method == "DELETE":
            return jsonify({"success": False, "error": exc.code, "message": exc.message}), 404
        flash(exc.message, "danger")
        return redirect(url_for("calendar.my_calendar"))

    if request.is_json or request.method == "DELETE":
        return jsonify({"success": True, "event": _event_json(event)})
    flash("Tapaaminen peruutettu.", "success")
    return redirect(url_for("calendar.my_calendar"))
