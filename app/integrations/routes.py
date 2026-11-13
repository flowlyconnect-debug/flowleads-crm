"""Settings page for CRM connections and the export actions on leads."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app.core.audit import log_audit
from app.core.messages_fi import fi_message
from app.core.permissions import require_role
from app.core.tenant import org_query_suffix, resolve_organization_id
from app.extensions import db
from app.integrations.models import PROVIDER_LABELS, PROVIDERS
from app.integrations.services import (
    MAX_PUSH,
    CrmExportError,
    connect,
    connections,
    disconnect,
    import_file,
    push_leads,
)

integrations_bp = Blueprint("integrations", __name__)

UI_ROLES = ("superadmin", "admin", "user")


@integrations_bp.app_context_processor
def _crm_template_helpers():
    def crm_providers_for(organization_id):
        """Connected CRMs of the organization, for export buttons."""
        if not organization_id:
            return []
        from app.integrations.models import CrmConnection

        return [
            c.provider
            for c in CrmConnection.query.filter_by(organization_id=organization_id).order_by(CrmConnection.provider).all()
        ]

    def crm_links_for(lead_id):
        """{provider: CrmExportLink} of one lead."""
        from app.integrations.services import export_links

        return export_links([lead_id]).get(lead_id, {}) if lead_id else {}

    return {"crm_providers_for": crm_providers_for, "crm_links_for": crm_links_for, "crm_labels": PROVIDER_LABELS}


def _provider(provider: str) -> str:
    if provider not in PROVIDERS:
        abort(404)
    return provider


def _org_leads(organization_id: int, ids):
    from app.leads.models import Lead

    clean = []
    for raw in ids:
        try:
            clean.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not clean:
        return []
    return (
        Lead.query.options(joinedload(Lead.stage))
        .filter(Lead.organization_id == organization_id, Lead.id.in_(clean), Lead.status != "archived")
        .order_by(Lead.id.asc())
        .all()
    )


# --- settings ---------------------------------------------------------------------


@integrations_bp.route("/settings/integrations", methods=["GET"])
@login_required
@require_role("admin", "superadmin")
def settings_page():
    organization_id = resolve_organization_id()
    return render_template(
        "settings/integrations.html",
        connections=connections(organization_id),
        providers=PROVIDERS,
        labels=PROVIDER_LABELS,
        organization_id=organization_id,
        org_query=org_query_suffix(organization_id),
    )


@integrations_bp.route("/settings/integrations/<provider>/connect", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
def connect_route(provider: str):
    organization_id = resolve_organization_id()
    provider = _provider(provider)
    settings = {}
    if provider == "hubspot":
        settings = {
            "pipeline": (request.form.get("pipeline") or "").strip()[:64],
            "dealstage": (request.form.get("dealstage") or "").strip()[:64],
        }
    try:
        connection = connect(
            organization_id, provider, request.form.get("token") or "", user_id=current_user.id, settings=settings
        )
    except CrmExportError as exc:
        db.session.rollback()
        flash(fi_message(exc.message), "danger")
        return redirect(url_for("integrations.settings_page", **org_query_suffix(organization_id)))
    log_audit(
        "crm_connected",
        user_id=current_user.id,
        organization_id=organization_id,
        target_type="crm_connection",
        target_id=connection.id,
        metadata={"provider": provider},
    )
    db.session.commit()
    flash(f"{PROVIDER_LABELS[provider]} on yhdistetty. Voit nyt viedä liidejä suoraan liidin sivulta tai liidilistalta.", "success")
    return redirect(url_for("integrations.settings_page", **org_query_suffix(organization_id)))


@integrations_bp.route("/settings/integrations/<provider>/disconnect", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
def disconnect_route(provider: str):
    organization_id = resolve_organization_id()
    provider = _provider(provider)
    if disconnect(organization_id, provider):
        log_audit(
            "crm_disconnected",
            user_id=current_user.id,
            organization_id=organization_id,
            target_type="crm_connection",
            target_id=None,
            metadata={"provider": provider},
        )
        db.session.commit()
        flash(f"{PROVIDER_LABELS[provider]}-yhteys poistettu. Avain on poistettu FlowLeadsista.", "success")
    return redirect(url_for("integrations.settings_page", **org_query_suffix(organization_id)))


# --- export -------------------------------------------------------------------------


@integrations_bp.route("/leads/export/<provider>.csv", methods=["GET"])
@login_required
def export_file(provider: str):
    """The current lead list (same filters as "Vie CSV") or the ticked leads
    (`ids`) as an import file for the CRM."""
    if current_user.role not in UI_ROLES:
        abort(403)
    from app.leads.models import Lead
    from app.leads.routes import _filters_from_request
    from app.leads.services import LeadService

    provider = _provider(provider)
    organization_id = resolve_organization_id()
    ids = [part for part in (request.args.get("ids") or "").split(",") if part.strip()]
    if ids:
        leads = _org_leads(organization_id, ids)
    else:
        query = Lead.query.options(joinedload(Lead.stage))
        leads = LeadService._apply_filters(query, organization_id, _filters_from_request()).all()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        "﻿" + import_file(provider, leads),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=flowleads-{provider}-{date_str}.csv"},
    )


def _push(organization_id: int, provider: str, leads):
    try:
        result = push_leads(organization_id, provider, leads)
    except CrmExportError as exc:
        db.session.rollback()
        flash(fi_message(exc.message), "danger")
        return None
    label = PROVIDER_LABELS[provider]
    done = result["created"] + result["updated"]
    if done:
        parts = []
        if result["created"]:
            parts.append(f"{result['created']} uutta")
        if result["updated"]:
            parts.append(f"{result['updated']} päivitetty")
        flash(f"Viety {label}iin: {', '.join(parts)}.", "success")
    for error in result["errors"][:3]:
        flash(f"Liidin {error['lead_id']} vienti epäonnistui: {fi_message(error['error'])}", "danger")
    log_audit(
        "crm_export",
        user_id=current_user.id,
        organization_id=organization_id,
        target_type="lead",
        target_id=None,
        metadata={"provider": provider, **{k: result[k] for k in ("created", "updated", "failed")}},
    )
    db.session.commit()
    return result


@integrations_bp.route("/leads/<int:lead_id>/crm/<provider>", methods=["POST"])
@login_required
def push_one(lead_id: int, provider: str):
    if current_user.role not in UI_ROLES:
        abort(403)
    provider = _provider(provider)
    organization_id = resolve_organization_id()
    leads = _org_leads(organization_id, [lead_id])
    if not leads:
        abort(404)
    _push(organization_id, provider, leads)
    return redirect(url_for("leads.detail", lead_id=lead_id, **org_query_suffix(organization_id)))


@integrations_bp.route("/leads/crm/<provider>", methods=["POST"])
@login_required
def push_many(provider: str):
    if current_user.role not in UI_ROLES:
        abort(403)
    provider = _provider(provider)
    organization_id = resolve_organization_id()
    # the lead list's bulk form sends a JSON list ('["1","2"]'), forms "1,2"
    raw = (request.form.get("lead_ids") or "").strip().strip("[]").replace('"', "").replace("'", "")
    ids = [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()] or request.form.getlist("lead_id")
    leads = _org_leads(organization_id, ids)
    if not leads:
        flash("Valitse vähintään yksi liidi.", "warning")
    elif len(leads) > MAX_PUSH:
        flash(f"Vie enintään {MAX_PUSH} liidiä kerrallaan.", "warning")
    else:
        _push(organization_id, provider, leads)
    return redirect(url_for("leads.list_leads", **org_query_suffix(organization_id)))
