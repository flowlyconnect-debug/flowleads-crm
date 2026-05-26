from __future__ import annotations

from flask import Blueprint, Response, abort, flash, render_template, request

from app.extensions import db, limiter
from app.proposals.forms import PublicAcceptForm, PublicDeclineForm
from app.proposals.pdf import ProposalPDFService
from app.proposals.services import ProposalService, ProposalServiceError

proposals_public_bp = Blueprint("proposals_public", __name__)


def _request_meta() -> dict:
    return {
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr),
        "user_agent": (request.user_agent.string or "")[:500],
    }


@proposals_public_bp.route("/p/<view_token>", methods=["GET"])
@limiter.limit("60/hour")
def view_proposal(view_token: str):
    try:
        result = ProposalService.record_view(view_token, _request_meta())
        db.session.commit()
    except ProposalServiceError:
        db.session.rollback()
        abort(404)

    proposal = result["proposal"]
    if result.get("state") == "expired":
        return render_template(
            "proposals/public.html",
            expired=True,
            proposal_data=ProposalService.public_proposal_dict(proposal),
        )

    public = ProposalService.public_proposal_dict(proposal)
    accept_form = PublicAcceptForm()
    decline_form = PublicDeclineForm()
    return render_template(
        "proposals/public.html",
        expired=False,
        proposal_data=public,
        view_token=view_token,
        accept_form=accept_form,
        decline_form=decline_form,
        readonly=proposal.status in ("accepted", "declined"),
    )


@proposals_public_bp.route("/p/<view_token>/accept", methods=["POST"])
@limiter.limit("20/hour")
def accept_proposal(view_token: str):
    form = PublicAcceptForm()
    if not form.validate_on_submit():
        flash("Tarkista allekirjoituksen nimi.", "danger")
        return _redirect_view(view_token)
    try:
        ProposalService.accept(view_token, form.signature_name.data, _request_meta())
        db.session.commit()
        flash("Tarjous hyväksytty. Kiitos!", "success")
    except ProposalServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return _redirect_view(view_token)


@proposals_public_bp.route("/p/<view_token>/decline", methods=["POST"])
@limiter.limit("20/hour")
def decline_proposal(view_token: str):
    form = PublicDeclineForm()
    if not form.validate_on_submit():
        return _redirect_view(view_token)
    try:
        ProposalService.decline(view_token, form.reason.data)
        db.session.commit()
        flash("Tarjous hylätty.", "info")
    except ProposalServiceError as exc:
        db.session.rollback()
        flash(exc.message, "danger")
    return _redirect_view(view_token)


def _redirect_view(view_token: str):
    from flask import redirect, url_for

    return redirect(url_for("proposals_public.view_proposal", view_token=view_token))


@proposals_public_bp.route("/p/<view_token>/pdf", methods=["GET"])
@limiter.limit("30/hour")
def public_pdf(view_token: str):
    from app.proposals.services import get_proposal_by_token

    proposal = get_proposal_by_token(view_token)
    if not proposal:
        abort(404)
    try:
        ProposalService.record_view(view_token, _request_meta())
        db.session.commit()
    except ProposalServiceError:
        db.session.rollback()
    content = ProposalPDFService.generate(proposal)
    mimetype = "application/pdf" if content[:4] == b"%PDF" else "text/html"
    ext = "pdf" if mimetype == "application/pdf" else "html"
    return Response(
        content,
        mimetype=mimetype,
        headers={"Content-Disposition": f'inline; filename="proposal.{ext}"'},
    )
