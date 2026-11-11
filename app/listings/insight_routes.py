"""Isännöitsijät (property managers) and the housing-company card."""

from __future__ import annotations

from flask import Blueprint, abort, render_template, request
from flask_login import current_user, login_required

from app.core.tenant import org_query_suffix, resolve_organization_id
from app.listings.insight_services import STATE_LABELS_FI, get_company_card, get_manager, list_managers

insights_bp = Blueprint("insights", __name__)

UI_ROLES = ("superadmin", "admin", "user")


@insights_bp.before_request
@login_required
def _ui_users_only():
    if current_user.role not in UI_ROLES:
        abort(403)


@insights_bp.route("/managers", methods=["GET"])
def managers():
    organization_id = resolve_organization_id()
    search = (request.args.get("search") or "").strip()
    rows = list_managers(organization_id, search)
    return render_template(
        "insights/managers.html",
        managers=rows,
        search=search,
        organization_id=organization_id,
        org_query=org_query_suffix(organization_id),
    )


@insights_bp.route("/managers/view", methods=["GET"])
def manager_detail():
    organization_id = resolve_organization_id()
    manager = get_manager(organization_id, request.args.get("name") or "")
    if manager is None:
        abort(404)
    return render_template(
        "insights/manager_detail.html",
        manager=manager,
        state_labels=STATE_LABELS_FI,
        organization_id=organization_id,
        org_query=org_query_suffix(organization_id),
    )


@insights_bp.route("/housing-companies/<int:listing_id>", methods=["GET"])
def company_card(listing_id: int):
    organization_id = resolve_organization_id()
    card = get_company_card(organization_id, listing_id)
    if card is None:
        abort(404)
    from app.tasks.models import Task

    lead_ids = [lead.id for lead in card["leads"]]
    open_tasks = (
        Task.query.filter(
            Task.organization_id == organization_id,
            Task.lead_id.in_(lead_ids),
            Task.status.in_(("pending", "in_progress")),
        )
        .order_by(Task.due_date.asc())
        .all()
        if lead_ids
        else []
    )
    return render_template(
        "insights/company_card.html",
        card=card,
        open_tasks=open_tasks,
        organization_id=organization_id,
        org_query=org_query_suffix(organization_id),
    )
