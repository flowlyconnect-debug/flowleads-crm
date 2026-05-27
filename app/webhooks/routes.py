from __future__ import annotations

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.core.permissions import require_role
from app.extensions import db
from app.leads.permissions import resolve_organization_id
from app.webhooks.encryption import WebhookEncryptionError, encrypt_webhook_secret
from app.webhooks.forms import WebhookEndpointForm
from app.webhooks.models import WebhookDelivery, WebhookEndpoint
from app.webhooks.payloads import (
    WEBHOOK_EVENT_GROUPS,
    WEBHOOK_EVENT_GROUP_LABELS,
    WEBHOOK_EVENTS,
)
from app.webhooks.services import WebhookService


def _org_query_suffix(organization_id: int) -> dict:
    if current_user.is_superadmin():
        return {"organization_id": organization_id}
    return {}


def _events_from_request():
    events = request.form.getlist("events")
    return [event for event in events if event in WEBHOOK_EVENTS]


def register_settings_webhook_routes(settings_bp):
    @settings_bp.route("/webhooks", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin")
    def webhooks_list():
        organization_id = resolve_organization_id()
        endpoints = (
            WebhookEndpoint.query.filter_by(organization_id=organization_id)
            .order_by(WebhookEndpoint.created_at.desc())
            .all()
        )
        form = WebhookEndpointForm()
        return render_template(
            "settings/webhooks.html",
            endpoints=endpoints,
            form=form,
            event_groups=WEBHOOK_EVENT_GROUPS,
            event_group_labels=WEBHOOK_EVENT_GROUP_LABELS,
            events_catalog=WEBHOOK_EVENTS,
            organization_id=organization_id,
            org_query=_org_query_suffix(organization_id),
            editing_endpoint=None,
        )

    @settings_bp.route("/webhooks", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    def webhooks_create():
        organization_id = resolve_organization_id()
        form = WebhookEndpointForm()
        events = _events_from_request()
        if not events:
            flash("Select at least one event.", "danger")
            return redirect(url_for("settings.webhooks_list", **_org_query_suffix(organization_id)))
        if not form.validate_on_submit():
            flash("Invalid webhook endpoint data.", "danger")
            return redirect(url_for("settings.webhooks_list", **_org_query_suffix(organization_id)))
        try:
            endpoint = WebhookEndpoint(
                organization_id=organization_id,
                name=(form.name.data or "").strip(),
                provider=form.provider.data,
                url_encrypted=encrypt_webhook_secret(form.url.data),
                secret_encrypted=encrypt_webhook_secret(form.secret.data or ""),
                is_active=bool(form.is_active.data),
                events=events,
                created_by=current_user.id,
            )
            db.session.add(endpoint)
            db.session.commit()
            flash("Webhook endpoint created.", "success")
        except WebhookEncryptionError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception:
            db.session.rollback()
            flash("Failed to create webhook endpoint.", "danger")
        return redirect(url_for("settings.webhooks_list", **_org_query_suffix(organization_id)))

    @settings_bp.route("/webhooks/<int:endpoint_id>/edit", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin")
    def webhooks_edit(endpoint_id: int):
        organization_id = resolve_organization_id()
        endpoint = WebhookEndpoint.query.filter_by(
            id=endpoint_id, organization_id=organization_id
        ).first()
        if not endpoint:
            abort(404)
        endpoints = (
            WebhookEndpoint.query.filter_by(organization_id=organization_id)
            .order_by(WebhookEndpoint.created_at.desc())
            .all()
        )
        form = WebhookEndpointForm(
            name=endpoint.name,
            provider=endpoint.provider,
            is_active=endpoint.is_active,
        )
        return render_template(
            "settings/webhooks.html",
            endpoints=endpoints,
            form=form,
            event_groups=WEBHOOK_EVENT_GROUPS,
            event_group_labels=WEBHOOK_EVENT_GROUP_LABELS,
            events_catalog=WEBHOOK_EVENTS,
            organization_id=organization_id,
            org_query=_org_query_suffix(organization_id),
            editing_endpoint=endpoint,
        )

    @settings_bp.route("/webhooks/<int:endpoint_id>", methods=["POST", "PUT"])
    @login_required
    @require_role("admin", "superadmin")
    def webhooks_update(endpoint_id: int):
        organization_id = resolve_organization_id()
        endpoint = WebhookEndpoint.query.filter_by(
            id=endpoint_id, organization_id=organization_id
        ).first()
        if not endpoint:
            abort(404)
        form = WebhookEndpointForm()
        events = _events_from_request()
        if not events:
            flash("Select at least one event.", "danger")
            return redirect(url_for("settings.webhooks_edit", endpoint_id=endpoint_id, **_org_query_suffix(organization_id)))
        if not form.validate_on_submit():
            flash("Invalid webhook endpoint data.", "danger")
            return redirect(url_for("settings.webhooks_edit", endpoint_id=endpoint_id, **_org_query_suffix(organization_id)))
        try:
            endpoint.name = (form.name.data or "").strip()
            endpoint.provider = form.provider.data
            endpoint.url_encrypted = encrypt_webhook_secret(form.url.data)
            endpoint.secret_encrypted = encrypt_webhook_secret(form.secret.data or "")
            endpoint.is_active = bool(form.is_active.data)
            endpoint.events = events
            db.session.commit()
            flash("Webhook endpoint updated.", "success")
        except WebhookEncryptionError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        except Exception:
            db.session.rollback()
            flash("Failed to update webhook endpoint.", "danger")
        return redirect(url_for("settings.webhooks_list", **_org_query_suffix(organization_id)))

    @settings_bp.route("/webhooks/<int:endpoint_id>", methods=["DELETE"])
    @login_required
    @require_role("admin", "superadmin")
    def webhooks_delete(endpoint_id: int):
        organization_id = resolve_organization_id()
        endpoint = WebhookEndpoint.query.filter_by(
            id=endpoint_id, organization_id=organization_id
        ).first()
        if not endpoint:
            abort(404)
        db.session.delete(endpoint)
        db.session.commit()
        flash("Webhook endpoint deleted.", "success")
        return redirect(url_for("settings.webhooks_list", **_org_query_suffix(organization_id)))

    @settings_bp.route("/webhooks/<int:endpoint_id>/delete", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    def webhooks_delete_post(endpoint_id: int):
        organization_id = resolve_organization_id()
        endpoint = WebhookEndpoint.query.filter_by(
            id=endpoint_id, organization_id=organization_id
        ).first()
        if not endpoint:
            abort(404)
        db.session.delete(endpoint)
        db.session.commit()
        flash("Webhook endpoint deleted.", "success")
        return redirect(url_for("settings.webhooks_list", **_org_query_suffix(organization_id)))

    @settings_bp.route("/webhooks/<int:endpoint_id>/test", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    def webhooks_test(endpoint_id: int):
        organization_id = resolve_organization_id()
        endpoint = WebhookEndpoint.query.filter_by(
            id=endpoint_id, organization_id=organization_id
        ).first()
        if not endpoint:
            abort(404)
        result = WebhookService.send_test(endpoint_id, current_user.id)
        db.session.commit()
        if result.get("success"):
            flash("Test webhook sent successfully.", "success")
        else:
            flash("Test webhook failed.", "danger")
        return redirect(url_for("settings.webhooks_list", **_org_query_suffix(organization_id)))

    @settings_bp.route("/webhooks/<int:endpoint_id>/deliveries", methods=["GET"])
    @login_required
    @require_role("admin", "superadmin")
    def webhooks_deliveries(endpoint_id: int):
        organization_id = resolve_organization_id()
        endpoint = WebhookEndpoint.query.filter_by(
            id=endpoint_id, organization_id=organization_id
        ).first()
        if not endpoint:
            abort(404)
        deliveries = (
            WebhookDelivery.query.filter_by(
                endpoint_id=endpoint_id,
                organization_id=organization_id,
            )
            .order_by(WebhookDelivery.created_at.desc())
            .limit(100)
            .all()
        )
        return render_template(
            "settings/webhook_deliveries.html",
            endpoint=endpoint,
            deliveries=deliveries,
            organization_id=organization_id,
            org_query=_org_query_suffix(organization_id),
        )

    @settings_bp.route("/webhooks/deliveries/<int:delivery_id>/retry", methods=["POST"])
    @login_required
    @require_role("admin", "superadmin")
    def webhooks_retry_delivery(delivery_id: int):
        organization_id = resolve_organization_id()
        delivery = (
            WebhookDelivery.query.filter_by(
                id=delivery_id,
                organization_id=organization_id,
            )
            .first()
        )
        if not delivery:
            abort(404)
        if delivery.status != "delivered":
            delivery.retry_count = min((delivery.retry_count or 0) + 1, 1)
            WebhookService.send_delivery(delivery.id)
            db.session.commit()
            flash("Webhook delivery retry executed.", "success")
        return redirect(
            url_for(
                "settings.webhooks_deliveries",
                endpoint_id=delivery.endpoint_id,
                **_org_query_suffix(organization_id),
            )
        )

