"""Public price list, the customer's Tilaus page and the superadmin controls."""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.billing.plans import PLANS, PLANS_BY_KEY, TERMS, TRIAL_DAYS, VAT_PERCENT, price_label
from app.billing.services import (
    BillingError,
    admin_set,
    cancel,
    contact_request,
    resume,
    subscribe,
    subscription_state,
)
from app.core.audit import log_audit
from app.core.messages_fi import fi_message
from app.core.permissions import require_2fa, require_role
from app.core.tenant import org_query_suffix, resolve_organization_id
from app.extensions import db, limiter

billing_bp = Blueprint("billing", __name__)


@billing_bp.app_context_processor
def _subscription_banner():
    """The trial / ended-contract notice at the top of every app page."""

    def subscription_banner():
        try:
            if not current_user.is_authenticated or current_user.role not in ("admin", "user"):
                return None
            organization = current_user.organization
        except Exception:  # no request (e-mail rendering) or no user loaded
            return None
        if organization is None:
            return None
        state = subscription_state(organization)
        if state.status == "active":
            return None
        return state

    return {"subscription_banner": subscription_banner, "plan_price_label": price_label}


# --- public price list ------------------------------------------------------------------


@billing_bp.route("/hinnasto", methods=["GET"])
def pricing():
    return render_template(
        "billing/pricing.html",
        plans=PLANS,
        terms=TERMS,
        trial_days=TRIAL_DAYS,
        vat=VAT_PERCENT,
        sent=request.args.get("sent") == "1",
        chosen=request.args.get("paketti") if request.args.get("paketti") in PLANS_BY_KEY else "",
    )


@billing_bp.route("/hinnasto/kokeilu", methods=["POST"])
@limiter.limit("5 per hour", methods=["POST"])
def pricing_contact():
    """A trial request from the price list. The honeypot field `website` is
    hidden from people; a bot that fills it gets the thank-you page and no email."""
    if (request.form.get("website") or "").strip():
        return redirect(url_for("billing.pricing", sent=1, _anchor="kokeilu"))
    try:
        contact_request(request.form, ip=request.remote_addr)
    except BillingError as exc:
        db.session.rollback()
        flash(fi_message(exc.message), "danger")
        return redirect(url_for("billing.pricing", paketti=request.form.get("plan") or None, _anchor="kokeilu"))
    return redirect(url_for("billing.pricing", sent=1, _anchor="kokeilu"))


# --- the customer's subscription --------------------------------------------------------


def _organization():
    from app.users.models import Organization

    organization = db.session.get(Organization, resolve_organization_id())
    if organization is None:
        abort(404)
    return organization


@billing_bp.route("/billing", methods=["GET"])
@login_required
@require_role("admin", "user", "superadmin")
def subscription_page():
    organization = _organization()
    from app.users.models import User

    return render_template(
        "billing/subscription.html",
        organization=organization,
        state=subscription_state(organization),
        plans=PLANS,
        plans_by_key=PLANS_BY_KEY,
        terms=TERMS,
        vat=VAT_PERCENT,
        user_count=User.query.filter_by(organization_id=organization.id, is_active=True).count(),
        can_manage=current_user.role in ("admin", "superadmin"),
        org_query=org_query_suffix(organization.id),
    )


def _back(organization):
    return redirect(url_for("billing.subscription_page", **org_query_suffix(organization.id)))


@billing_bp.route("/billing/subscribe", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
@require_2fa
def subscribe_route():
    organization = _organization()
    if request.form.get("accept_terms") != "1":
        flash("Hyväksy sopimusehdot ennen tilausta.", "warning")
        return _back(organization)
    plan_key = request.form.get("plan") or ""
    try:
        subscribe(organization, plan_key, actor=current_user)
    except BillingError as exc:
        db.session.rollback()
        flash(fi_message(exc.message), "warning")
        return _back(organization)
    log_audit(
        "subscription_ordered",
        user_id=current_user.id,
        organization_id=organization.id,
        target_type="organization",
        target_id=organization.id,
        metadata={"plan": plan_key},
    )
    db.session.commit()
    flash(f"Kiitos! Kuukausisopimus ({PLANS_BY_KEY[plan_key]['name']}) on voimassa. Lasku lähetetään sähköpostilla.", "success")
    return _back(organization)


@billing_bp.route("/billing/cancel", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
@require_2fa
def cancel_route():
    organization = _organization()
    try:
        ends = cancel(organization, actor=current_user)
    except BillingError as exc:
        db.session.rollback()
        flash(fi_message(exc.message), "warning")
        return _back(organization)
    log_audit(
        "subscription_cancelled",
        user_id=current_user.id,
        organization_id=organization.id,
        target_type="organization",
        target_id=organization.id,
        metadata={"cancel_at": ends.isoformat()},
    )
    db.session.commit()
    from app import to_helsinki
    from datetime import timedelta

    flash(
        f"Sopimus irtisanottu. Palvelu on käytössäsi {to_helsinki(ends - timedelta(seconds=1)).strftime('%d.%m.%Y')} asti, "
        "eikä seuraavaa kuukautta laskuteta.",
        "success",
    )
    return _back(organization)


@billing_bp.route("/billing/resume", methods=["POST"])
@login_required
@require_role("admin", "superadmin")
@require_2fa
def resume_route():
    organization = _organization()
    try:
        resume(organization, actor=current_user)
    except BillingError as exc:
        db.session.rollback()
        flash(fi_message(exc.message), "warning")
        return _back(organization)
    log_audit("subscription_resumed", user_id=current_user.id, organization_id=organization.id, target_type="organization", target_id=organization.id)
    db.session.commit()
    flash("Irtisanominen peruttu. Sopimus jatkuu.", "success")
    return _back(organization)


@billing_bp.route("/admin/customers/<int:organization_id>/subscription", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def admin_subscription_route(organization_id: int):
    from app.users.models import Organization

    organization = db.session.get(Organization, organization_id)
    if organization is None:
        abort(404)
    action = request.form.get("action") or ""
    try:
        message = admin_set(organization, action, plan_key=request.form.get("plan") or None)
    except BillingError as exc:
        db.session.rollback()
        flash(fi_message(exc.message), "danger")
        return redirect(url_for("admin.customers_list"))
    log_audit(
        "subscription_admin_change",
        user_id=current_user.id,
        organization_id=organization.id,
        target_type="organization",
        target_id=organization.id,
        metadata={"action": action, "plan": organization.plan, "status": organization.subscription_status},
    )
    db.session.commit()
    flash(f"{organization.name}: {message}", "success")
    return redirect(url_for("admin.customers_list"))
