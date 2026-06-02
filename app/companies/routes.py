from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.companies import companies_bp, contacts_bp
from app.companies.services import (
    CLIENT_FILTER_THRESHOLD,
    COMPANY_TYPE_FILTERS,
    CompanyService,
    CompanyServiceError,
    relative_created_fi,
)
from app.core.tenant import resolve_organization_id
from app.extensions import db


UI_ROLES = ("superadmin", "admin", "user")


def _require_ui_role():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in UI_ROLES:
        abort(403)


def _list_url(**kwargs):
    org_id = kwargs.pop("organization_id", None)
    if current_user.is_superadmin():
        if org_id is None:
            org_id = resolve_organization_id()
        return url_for("companies.list_companies", organization_id=org_id, **kwargs)
    return url_for("companies.list_companies", **kwargs)


@companies_bp.before_request
@login_required
def _block_unauthorized_companies():
    _require_ui_role()


@contacts_bp.before_request
@login_required
def _block_unauthorized_contacts():
    _require_ui_role()


@companies_bp.route("", methods=["GET"])
def list_companies():
    organization_id = resolve_organization_id()

    type_filter = request.args.get("type", "").strip() or None
    valid_types = {t for t, _ in COMPANY_TYPE_FILTERS if t}
    if type_filter and type_filter not in valid_types:
        type_filter = None

    search = (request.args.get("q") or "").strip() or None

    from app.companies.models import Company

    total_count = Company.query.filter_by(organization_id=organization_id).count()
    use_client_filter = total_count < CLIENT_FILTER_THRESHOLD

    if use_client_filter:
        rows, _ = CompanyService.list_companies_for_index(organization_id)
    else:
        rows, _ = CompanyService.list_companies_for_index(
            organization_id,
            type_filter=type_filter,
            search=search,
        )

    return render_template(
        "companies/index.html",
        rows=rows,
        total_count=total_count,
        organization_id=organization_id,
        active_type=type_filter or "",
        search_query=search or "",
        use_client_filter=use_client_filter,
        type_filters=COMPANY_TYPE_FILTERS,
        relative_created_fi=relative_created_fi,
    )


@companies_bp.route("/<int:company_id>", methods=["GET"])
def detail(company_id: int):
    organization_id = resolve_organization_id()
    try:
        company = CompanyService.get_company_for_org(company_id, organization_id)
    except CompanyServiceError as exc:
        abort(404 if exc.code == "not_found" else 400)

    open_leads = CompanyService.list_open_leads(company_id, organization_id)
    open_leads_count = len(open_leads)

    return render_template(
        "companies/detail.html",
        company=company,
        organization_id=organization_id,
        open_leads=open_leads,
        open_leads_count=open_leads_count,
    )


@companies_bp.route("/<int:company_id>/delete", methods=["POST"])
def delete_company(company_id: int):
    organization_id = resolve_organization_id()
    try:
        CompanyService.delete_company(company_id, organization_id)
        db.session.commit()
    except CompanyServiceError as exc:
        db.session.rollback()
        abort(404 if exc.code == "not_found" else 400)

    flash("Yritys poistettu.", "success")
    return redirect(_list_url(organization_id=organization_id))


@contacts_bp.route("", methods=["GET"])
def list_contacts():
    organization_id = resolve_organization_id()
    from app.companies.models import Contact, Company

    contacts = (
        Contact.query.filter_by(organization_id=organization_id)
        .order_by(Contact.created_at.desc())
        .all()
    )

    companies_by_id = {c.id: c for c in Company.query.filter_by(organization_id=organization_id).all()}

    return render_template(
        "companies/contacts.html",
        contacts=contacts,
        companies_by_id=companies_by_id,
        organization_id=organization_id,
    )
