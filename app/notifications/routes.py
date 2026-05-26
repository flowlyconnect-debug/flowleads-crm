from flask import Blueprint, request
from flask_login import current_user, login_required

from app.core.errors import json_error, json_success
from app.extensions import db
from app.notifications.services import NotificationService, NotificationServiceError

from flask import request as flask_request

notifications_bp = Blueprint("notifications_api", __name__, url_prefix="/api")


@notifications_bp.context_processor
def inject_notification_count():
    from flask_login import current_user

    if not current_user.is_authenticated:
        return {}
    try:
        org_id = current_user.organization_id
        if current_user.is_superadmin():
            org_param = flask_request.args.get("organization_id")
            if org_param:
                org_id = int(org_param)
        if org_id is None:
            return {"nav_unread_count": 0}
        from app.notifications.services import NotificationService

        return {
            "nav_unread_count": NotificationService.count_unread(
                current_user.id, org_id
            )
        }
    except Exception:
        return {"nav_unread_count": 0}


@notifications_bp.route("/notifications", methods=["GET"])
@login_required
def list_notifications():
    if current_user.organization_id is None and not current_user.is_superadmin():
        return json_error("forbidden", "Access denied.", 403)
    org_id = current_user.organization_id
    if current_user.is_superadmin():
        org_param = request.args.get("organization_id")
        if org_param:
            org_id = int(org_param)
    if org_id is None:
        return json_error("validation_error", "organization_id required.", 400)
    data = NotificationService.get_for_user(current_user.id, org_id)
    return json_success(data)


@notifications_bp.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id: int):
    org_id = current_user.organization_id
    if current_user.is_superadmin():
        org_param = request.args.get("organization_id")
        if request.is_json and request.json:
            org_param = org_param or request.json.get("organization_id")
        if org_param:
            org_id = int(org_param)
    if org_id is None:
        return json_error("validation_error", "organization_id required.", 400)
    try:
        NotificationService.mark_read(notification_id, current_user.id, org_id)
        db.session.commit()
        return json_success({"id": notification_id, "is_read": True})
    except NotificationServiceError as exc:
        db.session.rollback()
        return json_error(exc.code, exc.message, 404)


@notifications_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def mark_all_notifications_read():
    org_id = current_user.organization_id
    if current_user.is_superadmin():
        org_param = request.args.get("organization_id")
        if request.is_json and request.json:
            org_param = org_param or request.json.get("organization_id")
        if org_param:
            org_id = int(org_param)
    if org_id is None:
        return json_error("validation_error", "organization_id required.", 400)
    count = NotificationService.mark_all_read(current_user.id, org_id)
    db.session.commit()
    return json_success({"marked_read": count})
