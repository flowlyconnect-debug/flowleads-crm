"""Kohteet page: the organization's available targets (catalogue matches that
are not leads yet) and the actions that move them to leads or hide them."""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.core.audit import log_audit
from app.core.tenant import org_query_suffix, resolve_organization_id
from app.extensions import db
from app.listings.target_services import (
    TABS,
    count_targets,
    dismiss_targets,
    list_targets,
    restore_targets,
    target_row,
    transfer_targets,
)
from app.search.profile_services import list_profiles

targets_bp = Blueprint("targets", __name__, url_prefix="/targets")

UI_ROLES = ("superadmin", "admin", "user")


@targets_bp.before_request
@login_required
def _ui_users_only():
    if current_user.role not in UI_ROLES:
        abort(403)


def _back(organization_id: int):
    params = dict(org_query_suffix(organization_id))
    tab = request.form.get("tab")
    if tab in TABS and tab != "available":
        params["tab"] = tab
    for key in ("search", "profile_id", "page"):
        value = (request.form.get(key) or "").strip()
        if value:
            params[key] = value
    return redirect(url_for("targets.index", **params))


def _selected_ids() -> list[str]:
    """A row's own button sends `single_id`; the bulk bar sends the ticked rows."""
    single = (request.form.get("single_id") or "").strip()
    return [single] if single else request.form.getlist("match_ids")


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


@targets_bp.route("", methods=["GET"])
def index():
    organization_id = resolve_organization_id()
    tab = request.args.get("tab")
    if tab not in TABS:
        tab = "available"
    profiles = list_profiles(organization_id)
    profile_id = request.args.get("profile_id", type=int)
    if profile_id not in {p.id for p in profiles}:
        profile_id = None
    search = (request.args.get("search") or "").strip()
    pagination = list_targets(
        organization_id,
        status=TABS[tab],
        profile_id=profile_id,
        search=search,
        page=request.args.get("page", 1, type=int) or 1,
    )
    if not pagination.items and pagination.total and pagination.page > 1:
        # the last page emptied out (everything on it was moved or hidden)
        args = request.args.to_dict()
        args["page"] = pagination.pages or 1
        return redirect(url_for("targets.index", **args))
    return render_template(
        "targets/index.html",
        rows=[target_row(match) for match in pagination.items],
        pagination=pagination,
        tab=tab,
        search=search,
        profile_id=profile_id,
        profiles=profiles,
        available_count=count_targets(organization_id, "pending"),
        hidden_count=count_targets(organization_id, "dismissed"),
        organization_id=organization_id,
        org_query=org_query_suffix(organization_id),
        can_edit_profiles=current_user.role in ("admin", "superadmin"),
    )


@targets_bp.route("/transfer", methods=["POST"])
def transfer():
    organization_id = resolve_organization_id()
    match_ids = _selected_ids()
    if not match_ids:
        flash("Valitse vähintään yksi kohde.", "warning")
        return _back(organization_id)
    result = transfer_targets(organization_id, match_ids)
    log_audit(
        "targets_transferred",
        user_id=current_user.id,
        organization_id=organization_id,
        target_type="listing_match",
        target_id=None,
        metadata={k: result[k] for k in ("created", "merged", "failed")} | {"lead_ids": result["lead_ids"][:50]},
    )
    db.session.commit()

    parts = []
    if result["created"]:
        parts.append(_plural(result["created"], "kohde siirretty liidiksi", "kohdetta siirretty liideiksi"))
    if result["merged"]:
        parts.append(
            _plural(
                result["merged"],
                "kohde yhdistetty saman taloyhtiön liidiin",
                "kohdetta yhdistetty saman taloyhtiön liidiin",
            )
        )
    if parts:
        flash(", ".join(parts).capitalize() + ".", "success")
    if result["failed"]:
        flash(f"{result['failed']} kohteen siirto epäonnistui.", "danger")
    if not parts and not result["failed"]:
        flash("Kohteet oli jo käsitelty.", "info")
    return _back(organization_id)


@targets_bp.route("/dismiss", methods=["POST"])
def dismiss():
    organization_id = resolve_organization_id()
    count = dismiss_targets(organization_id, _selected_ids())
    if count:
        flash(_plural(count, "kohde piilotettu", "kohdetta piilotettu") + ".", "success")
    else:
        flash("Valitse vähintään yksi kohde.", "warning")
    return _back(organization_id)


@targets_bp.route("/restore", methods=["POST"])
def restore():
    organization_id = resolve_organization_id()
    count = restore_targets(organization_id, _selected_ids())
    if count:
        flash(_plural(count, "kohde palautettu", "kohdetta palautettu") + ".", "success")
    return _back(organization_id)
