"""Kohteet page: the organization's available targets (catalogue matches that
are not leads yet) and the actions that move them to leads or hide them."""

from __future__ import annotations

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.core.audit import log_audit
from app.core.tenant import org_query_suffix, resolve_organization_id
from app.extensions import db
from app.listings.target_services import (
    SavedSearchError,
    get_saved_search,
    list_saved_searches,
    save_search,
    TargetFetchCooldown,
    count_reserve,
    fetch_enabled,
    fetch_targets,
    next_fetch_time,
    FILTER_KEYS,
    SORT_OPTIONS,
    TABS,
    target_filter_options,
    organization_wanted_types,
    count_targets,
    dismiss_targets,
    list_targets,
    restore_targets,
    target_rows,
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
    for key in ("search", "profile_id", "page", *FILTER_KEYS):
        value = (request.form.get(key) or "").strip()
        if value:
            params[key] = value
    return redirect(url_for("targets.index", **params))


_TRANSFERRED_KEY = "targets_transferred_lead_ids"


def _valid_assignee(organization_id: int, user_id: int | None) -> int | None:
    """Owner for the new leads. Admins may pick anyone in the organization;
    a salesperson's transfers become their own leads."""
    from app.users.models import User

    if current_user.role == "user":
        return current_user.id
    if not user_id:
        return None
    user = db.session.get(User, user_id)
    if user is None or user.organization_id != organization_id or not user.is_active:
        return None
    return user.id


def _selected_ids() -> list[str]:
    """A row's own button sends `single_id`; the bulk bar sends the ticked rows."""
    single = (request.form.get("single_id") or "").strip()
    return [single] if single else request.form.getlist("match_ids")


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _page_state():
    """The organization, tab, profile, search and filters of the request:
    shared by the list and the map."""
    organization_id = resolve_organization_id()
    tab = request.args.get("tab")
    if tab not in TABS:
        tab = "available"
    profiles = list_profiles(organization_id)
    profile_id = request.args.get("profile_id", type=int)
    if profile_id not in {p.id for p in profiles}:
        profile_id = None
    search = (request.args.get("search") or "").strip()
    filters = {
        key: (request.args.get(key) or "").strip()
        for key in FILTER_KEYS
        if (request.args.get(key) or "").strip()
    }
    return organization_id, tab, profiles, profile_id, search, filters


def _current_params(search: str, profile_id, filters: dict) -> dict:
    params = dict(filters)
    if search:
        params["search"] = search
    if profile_id:
        params["profile_id"] = str(profile_id)
    return params


_ASK_KEY = "targets_ask"


@targets_bp.route("/ask", methods=["GET"])
def ask():
    """"Kerro mitä etsit": turn the text into the filter bar's filters and show
    the list with them. The page tells how the text was read."""
    from app.listings.nl_search import interpret

    organization_id, tab, _profiles, profile_id, _search, _filters = _page_state()
    text = " ".join((request.args.get("q") or "").split())[:300]
    params = dict(org_query_suffix(organization_id))
    if tab != "available":
        params["tab"] = tab
    if profile_id:
        params["profile_id"] = profile_id
    if not text:
        return redirect(url_for("targets.index", **params))
    result = interpret(text, target_filter_options(organization_id, TABS[tab]))
    params.update(result["params"])
    if result["search"]:
        params["search"] = result["search"]
    session[_ASK_KEY] = {
        "text": text,
        "recognized": result["recognized"][:8],
        "unknown": result["unknown"][:8],
        "source": result["source"],
    }
    return redirect(url_for("targets.index", **params))


@targets_bp.route("/map", methods=["GET"])
def map_view():
    """The same targets as the list (same tab, search and filters) on a map."""
    from flask import current_app

    from app.listings.geo import MAX_MAP_POINTS, map_points

    organization_id, tab, profiles, profile_id, search, filters = _page_state()
    pagination = list_targets(
        organization_id,
        status=TABS[tab],
        profile_id=profile_id,
        search=search,
        filters=filters,
        page=1,
        per_page=MAX_MAP_POINTS,
    )
    rows = target_rows(organization_id, pagination.items, organization_wanted_types(organization_id))
    points = map_points(rows)
    params = _current_params(search, profile_id, filters)
    if tab != "available":
        params["tab"] = tab
    return render_template(
        "targets/map.html",
        points=points,
        total=pagination.total,
        truncated=pagination.total > MAX_MAP_POINTS,
        approximate=sum(1 for p in points if p["precision"] in ("city", "region")),
        missing=len(rows) - len(points),
        tab=tab,
        filters=filters,
        search=search,
        list_params=params,
        tile_url=current_app.config.get("MAP_TILE_URL") or "",
        tile_attribution=current_app.config.get("MAP_TILE_ATTRIBUTION") or "",
        organization_id=organization_id,
        org_query=org_query_suffix(organization_id),
    )


@targets_bp.route("", methods=["GET"])
def index():
    organization_id, tab, profiles, profile_id, search, filters = _page_state()
    pagination = list_targets(
        organization_id,
        status=TABS[tab],
        profile_id=profile_id,
        search=search,
        filters=filters,
        page=request.args.get("page", 1, type=int) or 1,
    )
    if not pagination.items and pagination.total and pagination.page > 1:
        # the last page emptied out (everything on it was moved or hidden)
        args = request.args.to_dict()
        args["page"] = pagination.pages or 1
        return redirect(url_for("targets.index", **args))
    # leads created by the transfer that just happened: shown once, with links
    transferred_leads = []
    lead_ids = session.pop(_TRANSFERRED_KEY, None)
    if lead_ids:
        from app.leads.models import Lead

        transferred_leads = (
            Lead.query.filter(Lead.organization_id == organization_id, Lead.id.in_(lead_ids))
            .order_by(Lead.id.asc())
            .all()
        )
    can_assign = current_user.role in ("admin", "superadmin")
    assignees = []
    if can_assign:
        from app.users.models import User

        assignees = (
            User.query.filter_by(organization_id=organization_id, is_active=True)
            .order_by(User.email.asc())
            .all()
        )
    from app.tasks.settings import get_organization_settings

    digest_enabled = get_organization_settings(organization_id).targets_digest_enabled
    db.session.commit()  # the settings row is created on first use
    from app import to_helsinki
    from app.listings.services import _now

    now = _now()
    next_fetch = next_fetch_time(organization_id, now=now) if fetch_enabled() else None
    fetched = request.args.get("fetched", type=int)
    new_ids = set()
    if fetch_enabled():
        # rows released in the last few minutes carry an "Uusi" badge
        from datetime import timedelta

        for match in pagination.items:
            released = match.released_at
            if released is not None:
                if released.tzinfo is None:
                    released = released.replace(tzinfo=now.tzinfo)
                if now - released < timedelta(minutes=5):
                    new_ids.add(match.id)
    current_params = _current_params(search, profile_id, filters)
    asked = session.pop(_ASK_KEY, None)
    saved_searches = list_saved_searches(organization_id, current_user.id)
    wanted_types = organization_wanted_types(organization_id)
    return render_template(
        "targets/index.html",
        saved_searches=saved_searches,
        asked=asked,
        current_params=current_params,
        active_saved_id=next(
            (s.id for s in saved_searches if (s.params or {}) == current_params and current_params), None
        ),
        fetch_enabled=fetch_enabled() and bool(profiles),
        next_fetch_label=to_helsinki(next_fetch).strftime("%H.%M") if next_fetch else None,
        next_fetch_seconds=max(0, int((next_fetch - now).total_seconds())) if next_fetch else 0,
        fetched=fetched,
        new_ids=new_ids,
        reserve_count=count_reserve(organization_id) if current_user.is_superadmin() else None,
        digest_enabled=digest_enabled,
        filters=filters,
        filter_options=target_filter_options(organization_id, TABS[tab], filters),
        sort_options=SORT_OPTIONS,
        transferred_leads=transferred_leads,
        assignees=assignees,
        rows=target_rows(organization_id, pagination.items, wanted_types),
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
    assigned_to = _valid_assignee(organization_id, request.form.get("assigned_to", type=int))
    result = transfer_targets(organization_id, match_ids, assigned_to=assigned_to)
    if result["lead_ids"]:
        session[_TRANSFERRED_KEY] = result["lead_ids"][:25]
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


@targets_bp.route("/searches", methods=["POST"])
def save_search_route():
    """Save the filters now in use under a name."""
    organization_id = resolve_organization_id()
    try:
        saved = save_search(organization_id, current_user.id, request.form.get("name"), request.form)
        db.session.commit()
        flash(f"Haku tallennettu: {saved.name}. Saat ilmoituksen, kun siihen osuu uusia kohteita.", "success")
    except SavedSearchError as exc:
        db.session.rollback()
        flash(exc.message, "warning")
    return _back(organization_id)


@targets_bp.route("/searches/<int:search_id>/delete", methods=["POST"])
def delete_search_route(search_id: int):
    organization_id = resolve_organization_id()
    saved = get_saved_search(organization_id, current_user.id, search_id)
    if saved is None:
        abort(404)
    db.session.delete(saved)
    db.session.commit()
    flash("Tallennettu haku poistettu.", "success")
    return redirect(url_for("targets.index", **org_query_suffix(organization_id)))


@targets_bp.route("/searches/<int:search_id>/notify", methods=["POST"])
def toggle_search_notify_route(search_id: int):
    organization_id = resolve_organization_id()
    saved = get_saved_search(organization_id, current_user.id, search_id)
    if saved is None:
        abort(404)
    saved.notify = not saved.notify
    if saved.notify:
        from app.listings.services import _now

        saved.last_notified_at = _now()
    db.session.commit()
    flash(
        f"Ilmoitukset haulle {saved.name}: " + ("päällä." if saved.notify else "pois päältä."),
        "success",
    )
    return _back(organization_id)


@targets_bp.route("/fetch", methods=["POST"])
def fetch():
    """"Hae kohteita": release a batch of the organization's reserve targets.
    Answers JSON; the page shows its progress animation for `wait_seconds`
    and then reloads, by which time the targets are visible."""
    from app import to_helsinki

    organization_id = resolve_organization_id()
    if not fetch_enabled():
        return jsonify({"success": False, "message": "Toiminto ei ole käytössä."}), 404
    try:
        # the platform's own superadmin can demo the button without waiting an hour
        result = fetch_targets(organization_id, ignore_cooldown=current_user.is_superadmin())
    except TargetFetchCooldown as cooldown:
        db.session.rollback()
        label = to_helsinki(cooldown.next_fetch_at).strftime("%H.%M")
        return (
            jsonify(
                {
                    "success": False,
                    "code": "cooldown",
                    "message": f"Kohteita voi hakea kerran tunnissa. Seuraava haku on mahdollinen klo {label}.",
                }
            ),
            429,
        )
    log_audit(
        "targets_fetched",
        user_id=current_user.id,
        organization_id=organization_id,
        target_type="listing_match",
        target_id=None,
        metadata={"count": result["count"]},
    )
    db.session.commit()
    params = dict(org_query_suffix(organization_id))
    params["fetched"] = result["count"]
    return jsonify(
        {
            "success": True,
            "count": result["count"],
            "wait_seconds": result["wait_seconds"],
            "redirect": url_for("targets.index", **params),
        }
    )


@targets_bp.route("/digest", methods=["POST"])
def digest_setting():
    """Switch the daily "new targets" summary on or off for the organization."""
    if current_user.role not in ("admin", "superadmin"):
        abort(403)
    from app.tasks.settings import get_organization_settings

    organization_id = resolve_organization_id()
    settings = get_organization_settings(organization_id)
    settings.targets_digest_enabled = request.form.get("enabled") == "1"
    db.session.commit()
    flash(
        "Päiväkooste uusista kohteista on päällä."
        if settings.targets_digest_enabled
        else "Päiväkooste uusista kohteista on pois päältä.",
        "success",
    )
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
