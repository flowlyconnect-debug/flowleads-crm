from __future__ import annotations

import json
from datetime import date, timedelta

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.core.errors import json_error, json_success, wants_json_response
from app.core.permissions import require_2fa, require_role
from app.extensions import db
from app.leads.permissions import resolve_organization_id
from app.leads.services import LeadServiceError, get_lead_for_org
from app.proposals.forms import ProposalSettingsForm
from app.proposals.models import PROPOSAL_STATUSES
from app.proposals.pdf import ProposalPDFService
from app.proposals.services import ProposalService, ProposalServiceError, get_proposal_for_org

proposals_bp = Blueprint("proposals", __name__, url_prefix="/proposals")

UI_ROLES = ("superadmin", "admin", "user")

STATUS_COLORS = {
    "draft": "#94a3b8",
    "sent": "#3b82f6",
    "viewed": "#eab308",
    "accepted": "#22c55e",
    "declined": "#ef4444",
    "expired": "#f97316",
}


def _require_ui_role():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in UI_ROLES:
        abort(403)


def _org_query_suffix(organization_id: int) -> dict:
    if current_user.is_superadmin():
        return {"organization_id": organization_id}
    return {}


@proposals_bp.before_request
@login_required
def _guard():
    _require_ui_role()


@proposals_bp.route("", methods=["GET"])
@require_role(*UI_ROLES)
@require_2fa
def list_proposals():
    organization_id = resolve_organization_id()
    status = request.args.get("status") or None
    proposals = ProposalService.list_for_organization(organization_id, status=status)
    warn_date = date.today() + timedelta(days=3)
    return render_template(
        "proposals/list.html",
        proposals=proposals,
        statuses=PROPOSAL_STATUSES,
        status_filter=status,
        status_colors=STATUS_COLORS,
        warn_date=warn_date,
        organization_id=organization_id,
        org_query=_org_query_suffix(organization_id),
    )


@proposals_bp.route("", methods=["POST"])
@require_role(*UI_ROLES)
def create_proposal():
    organization_id = resolve_organization_id()
    if request.is_json:
        data = request.get_json(silent=True) or {}
        lead_id = data.get("lead_id")
    else:
        lead_id = request.form.get("lead_id", type=int)
        data = {
            "title": request.form.get("title"),
            "line_items": json.loads(request.form.get("line_items") or "[]"),
        }
    if not lead_id:
        flash("Lead is required.", "danger")
        return redirect(url_for("proposals.list_proposals", **_org_query_suffix(organization_id)))

    try:
        proposal = ProposalService.create(
            int(lead_id), data, current_user.id, organization_id
        )
        db.session.commit()
        if wants_json_response():
            return json_success({"id": proposal.id, "reference_number": proposal.reference_number})
        flash("Tarjous luotu.", "success")
        return redirect(
            url_for(
                "proposals.edit_proposal",
                proposal_id=proposal.id,
                **_org_query_suffix(organization_id),
            )
        )
    except (ProposalServiceError, LeadServiceError) as exc:
        db.session.rollback()
        msg = getattr(exc, "message", str(exc))
        if wants_json_response():
            return json_error(getattr(exc, "code", "error"), msg, 400)
        flash(msg, "danger")
        return redirect(url_for("proposals.list_proposals", **_org_query_suffix(organization_id)))


@proposals_bp.route("/<int:proposal_id>", methods=["GET"])
@require_role(*UI_ROLES)
@require_2fa
def edit_proposal(proposal_id: int):
    organization_id = resolve_organization_id()
    try:
        proposal = get_proposal_for_org(proposal_id, organization_id)
    except ProposalServiceError:
        abort(404)
    return render_template(
        "proposals/edit.html",
        proposal=proposal,
        status_colors=STATUS_COLORS,
        organization_id=organization_id,
        org_query=_org_query_suffix(organization_id),
    )


@proposals_bp.route("/<int:proposal_id>", methods=["PUT", "POST"])
@require_role(*UI_ROLES)
def update_proposal(proposal_id: int):
    organization_id = resolve_organization_id()
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = {
            "title": request.form.get("title"),
            "valid_until": request.form.get("valid_until"),
            "notes": request.form.get("notes"),
            "discount_percent": request.form.get("discount_percent"),
            "tax_percent": request.form.get("tax_percent"),
            "line_items": json.loads(request.form.get("line_items") or "[]"),
        }
    try:
        proposal = ProposalService.update(
            proposal_id, data, organization_id, current_user.id
        )
        db.session.commit()
        if wants_json_response():
            return json_success(
                {
                    "id": proposal.id,
                    "subtotal": str(proposal.subtotal),
                    "total": str(proposal.total),
                }
            )
        flash("Tarjous päivitetty.", "success")
    except ProposalServiceError as exc:
        db.session.rollback()
        if wants_json_response():
            return json_error(exc.code, exc.message, 400 if exc.code != "not_found" else 404)
        flash(exc.message, "danger")
    return redirect(
        url_for(
            "proposals.edit_proposal",
            proposal_id=proposal_id,
            **_org_query_suffix(organization_id),
        )
    )


@proposals_bp.route("/<int:proposal_id>", methods=["DELETE"])
@require_role(*UI_ROLES)
def delete_proposal(proposal_id: int):
    organization_id = resolve_organization_id()
    try:
        ProposalService.delete(proposal_id, organization_id)
        db.session.commit()
        flash("Tarjous poistettu.", "success")
    except ProposalServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(url_for("proposals.list_proposals", **_org_query_suffix(organization_id)))


@proposals_bp.route("/<int:proposal_id>/send", methods=["POST"])
@require_role(*UI_ROLES)
def send_proposal(proposal_id: int):
    organization_id = resolve_organization_id()
    try:
        public_url = ProposalService.send(proposal_id, current_user.id, organization_id)
        db.session.commit()
        flash(f"Tarjous lähetetty. Julkinen linkki: {public_url}", "success")
    except ProposalServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return redirect(
        url_for(
            "proposals.edit_proposal",
            proposal_id=proposal_id,
            **_org_query_suffix(organization_id),
        )
    )


@proposals_bp.route("/<int:proposal_id>/duplicate", methods=["POST"])
@require_role(*UI_ROLES)
def duplicate_proposal(proposal_id: int):
    organization_id = resolve_organization_id()
    try:
        copy = ProposalService.duplicate(proposal_id, organization_id, current_user.id)
        db.session.commit()
        flash("Kopio luotu luonnoksena.", "success")
        return redirect(
            url_for(
                "proposals.edit_proposal",
                proposal_id=copy.id,
                **_org_query_suffix(organization_id),
            )
        )
    except ProposalServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
        return redirect(
            url_for(
                "proposals.edit_proposal",
                proposal_id=proposal_id,
                **_org_query_suffix(organization_id),
            )
        )


@proposals_bp.route("/<int:proposal_id>/preview", methods=["GET"])
@require_role(*UI_ROLES)
def preview_proposal(proposal_id: int):
    organization_id = resolve_organization_id()
    try:
        proposal = get_proposal_for_org(proposal_id, organization_id)
    except ProposalServiceError:
        abort(404)
    html = ProposalPDFService.render_html(proposal)
    return Response(html, mimetype="text/html")


@proposals_bp.route("/<int:proposal_id>/pdf", methods=["GET"])
@require_role(*UI_ROLES)
def pdf_proposal(proposal_id: int):
    organization_id = resolve_organization_id()
    try:
        proposal = get_proposal_for_org(proposal_id, organization_id)
    except ProposalServiceError:
        abort(404)
    content = ProposalPDFService.generate(proposal)
    mimetype = "application/pdf" if content[:4] == b"%PDF" else "text/html"
    ext = "pdf" if mimetype == "application/pdf" else "html"
    return Response(
        content,
        mimetype=mimetype,
        headers={
            "Content-Disposition": f'inline; filename="{proposal.reference_number}.{ext}"'
        },
    )


def register_proposal_lead_routes(leads_bp):
    @leads_bp.route("/<int:lead_id>/proposals", methods=["GET"])
    @login_required
    @require_role(*UI_ROLES)
    def lead_proposals(lead_id: int):
        organization_id = resolve_organization_id()
        try:
            get_lead_for_org(lead_id, organization_id)
        except LeadServiceError:
            abort(404)
        summary = ProposalService.get_lead_proposals_summary(lead_id, organization_id)
        return jsonify(
            {
                "success": True,
                "proposals": [
                    {
                        "id": p.id,
                        "reference_number": p.reference_number,
                        "title": p.title,
                        "status": p.status,
                        "total": str(p.total),
                        "currency": p.currency,
                        "sent_at": p.sent_at.isoformat() if p.sent_at else None,
                        "valid_until": p.valid_until.isoformat() if p.valid_until else None,
                    }
                    for p in summary["proposals"]
                ],
                "accepted_total": str(summary["accepted_total"]),
            }
        )


def register_proposal_settings_routes(settings_bp):
    @settings_bp.route("/proposals", methods=["GET", "POST"])
    @login_required
    @require_role("admin", "superadmin")
    def proposals_settings():
        organization_id = resolve_organization_id()
        from app.proposals.settings import get_proposal_settings

        settings = get_proposal_settings(organization_id)
        template = ProposalService.get_template(organization_id)
        form = ProposalSettingsForm()
        if request.method == "GET":
            form.proposal_move_lead_to_won_on_accept.data = settings.proposal_move_lead_to_won_on_accept
            form.proposal_default_valid_days.data = settings.proposal_default_valid_days
            form.proposal_default_tax_percent.data = settings.proposal_default_tax_percent
            form.proposal_default_notes.data = settings.proposal_default_notes
            if template:
                form.default_valid_days.data = template.default_valid_days
                form.default_tax_percent.data = template.default_tax_percent
                form.default_notes.data = template.default_notes
                form.header_html.data = template.header_html
                form.footer_html.data = template.footer_html
        elif form.validate_on_submit():
            try:
                ProposalService.save_org_settings(
                    organization_id,
                    {
                        "proposal_move_lead_to_won_on_accept": form.proposal_move_lead_to_won_on_accept.data,
                        "proposal_default_valid_days": form.proposal_default_valid_days.data,
                        "proposal_default_tax_percent": form.proposal_default_tax_percent.data,
                        "proposal_default_notes": form.proposal_default_notes.data,
                    },
                )
                ProposalService.save_template(
                    organization_id,
                    {
                        "default_valid_days": form.default_valid_days.data or 30,
                        "default_tax_percent": form.default_tax_percent.data or 24,
                        "default_notes": form.default_notes.data,
                        "header_html": form.header_html.data,
                        "footer_html": form.footer_html.data,
                    },
                    current_user.id,
                )
                db.session.commit()
                flash("Tarjousasetukset tallennettu.", "success")
                return redirect(url_for("settings.proposals_settings"))
            except Exception as exc:
                db.session.rollback()
                flash(str(exc), "danger")

        return render_template("settings/proposals.html", form=form)
