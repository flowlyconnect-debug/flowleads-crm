from flask import abort, render_template
from flask_login import current_user, login_required

from app.companies import companies_bp, contacts_bp
from app.companies.services import CompanyService, CompanyServiceError
from app.core.tenant import resolve_organization_id


UI_ROLES = ("superadmin", "admin", "user")


def _require_ui_role():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in UI_ROLES:
        abort(403)


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

    from app.companies.models import Company

    companies = (
        Company.query.filter_by(organization_id=organization_id)
        .order_by(Company.created_at.desc())
        .all()
    )

    return render_template(
        "companies/index.html",
        companies=companies,
        organization_id=organization_id,
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

    # Simple activity feed stub: reuse lead activities.
    from app.leads.models import Lead

    activities = (
        Lead.query.filter_by(organization_id=organization_id, company_id=company_id)
        .all()
    )

    return render_template(
        "companies/detail.html",
        company=company,
        organization_id=organization_id,
        open_leads=open_leads,
        open_leads_count=open_leads_count,
    )


@contacts_bp.route("", methods=["GET"])
def list_contacts():
    organization_id = resolve_organization_id()
    from app.companies.models import Contact, Company

    contacts = (
        Contact.query.filter_by(organization_id=organization_id)
        .order_by(Contact.created_at.desc())
        .all()
    )

    # Attach company display for template convenience.
    companies_by_id = {c.id: c for c in Company.query.filter_by(organization_id=organization_id).all()}

    return render_template(
        "companies/contacts.html",
        contacts=contacts,
        companies_by_id=companies_by_id,
        organization_id=organization_id,
    )

