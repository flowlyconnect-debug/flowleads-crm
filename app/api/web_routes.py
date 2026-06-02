from flask import Blueprint, request
from flask_login import current_user, login_required

from app.core.errors import json_error, json_success
from app.extensions import db
from app.leads.services import LeadService, LeadServiceError

web_api_bp = Blueprint("web_api", __name__, url_prefix="/api")


@web_api_bp.route("/leads/<int:lead_id>/stage", methods=["PATCH"])
@login_required
def update_lead_stage(lead_id: int):
    if not request.is_json:
        return json_error("validation_error", "JSON body is required.", 400)

    payload = request.get_json(silent=True) or {}
    stage_id = payload.get("stage_id")
    if not stage_id:
        return json_error("validation_error", "stage_id is required.", 400)

    try:
        lead = LeadService.move_stage(
            lead_id=lead_id,
            stage_id=int(stage_id),
            organization_id=current_user.organization_id,
            user_id=current_user.id,
            lost_reason=payload.get("lost_reason"),
            lost_reason_note=payload.get("lost_reason_note"),
        )
        db.session.commit()
    except (TypeError, ValueError):
        db.session.rollback()
        return json_error("validation_error", "Invalid stage_id.", 400)
    except LeadServiceError as exc:
        db.session.rollback()
        status = 404 if exc.code == "not_found" else 400
        return json_error(exc.code, exc.message, status)

    return json_success({"lead_id": lead.id, "stage_id": lead.stage_id, "status": lead.status})
