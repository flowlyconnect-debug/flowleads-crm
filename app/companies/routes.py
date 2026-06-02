from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.companies import companies_bp, contacts_bp
from app.companies.forms import CompanyForm, ContactForm
from app.companies.models import Company
from app.companies.services import (
    CLIENT_FILTER_THRESHOLD,
    COMPANY_TYPE_FILTERS,
    CompanyService,
    CompanyServiceError,
    ContactService,
    relative_created_fi,
)
from app.core.tenant import organization_exists, parse_organization_id_param
from app.extensions import db


UI_ROLES = ("superadmin", "admin", "user")


def _require_ui_role():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in UI_ROLES:
        abort(403)


def _organization_id_for_request():
    """Resolve tenant org; redirect superadmin without organization_id instead of 400."""
    if current_user.is_superadmin():
        org_id = parse_organization_id_param()
        if org_id is None:
            flash(
                "Valitse organisaatio kojelaudalta ennen yritys- ja kontaktinäkymiä.",
                "warning",
            )
            return None, redirect(url_for("analytics.dashboard"))
        if not organization_exists(org_id):
            abort(404, description="Organization not found.")
        return org_id, None

    if current_user.organization_id is None:
        abort(403)
    return current_user.organization_id, None


def _list_url(**kwargs):
    org_id = kwargs.pop("organization_id", None)
    if current_user.is_superadmin():
        if org_id is None:
            org_id = parse_organization_id_param()
        if org_id is not None:
            return url_for("companies.list_companies", organization_id=org_id, **kwargs)
        return url_for("companies.list_companies", **kwargs)
    return url_for("companies.list_companies", **kwargs)


def _contacts_list_url(**kwargs):
    if current_user.is_superadmin():
        org_id = kwargs.get("organization_id") or parse_organization_id_param()
        if org_id is not None:
            return url_for("contacts.list_contacts", organization_id=org_id, **kwargs)
    return url_for("contacts.list_contacts", **kwargs)


def _populate_contact_company_choices(form: ContactForm, organization_id: int) -> None:
    companies = (
        Company.query.filter_by(organization_id=organization_id)
        .order_by(Company.name.asc())
        .all()
    )
    form.company_id.choices = [(0, "— Ei yritystä —")] + [
        (c.id, c.name) for c in companies
    ]


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
    organization_id, redirect_response = _organization_id_for_request()
    if redirect_response is not None:
        return redirect_response

    type_filter = request.args.get("type", "").strip() or None
    valid_types = {t for t, _ in COMPANY_TYPE_FILTERS if t}
    if type_filter and type_filter not in valid_types:
        type_filter = None

    search = (request.args.get("q") or "").strip() or None

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


@companies_bp.route("/new", methods=["GET", "POST"])
def new_company():
    organization_id, redirect_response = _organization_id_for_request()
    if redirect_response is not None:
        return redirect_response

    form = CompanyForm()
    if form.validate_on_submit():
        try:
            company = CompanyService.create_company(
                organization_id,
                form.name.data,
                type_=form.type.data,
                industry=form.industry.data,
                region=form.region.data,
                created_by=current_user.id,
            )
            db.session.commit()
            flash("Yritys luotu.", "success")
            detail_kwargs = {"company_id": company.id}
            if current_user.is_superadmin():
                detail_kwargs["organization_id"] = organization_id
            return redirect(url_for("companies.detail", **detail_kwargs))
        except CompanyServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")

    return render_template(
        "companies/form.html",
        form=form,
        organization_id=organization_id,
    )


@companies_bp.route("/<int:company_id>", methods=["GET"])
def detail(company_id: int):
    organization_id, redirect_response = _organization_id_for_request()
    if redirect_response is not None:
        return redirect_response

    try:
        company = CompanyService.get_company_for_org(company_id, organization_id)
    except CompanyServiceError as exc:
        if exc.code == "not_found":
            abort(404)
        flash("Yritystä ei voitu ladata.", "warning")
        return redirect(_list_url(organization_id=organization_id))

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
    organization_id, redirect_response = _organization_id_for_request()
    if redirect_response is not None:
        return redirect_response

    try:
        CompanyService.delete_company(company_id, organization_id)
        db.session.commit()
    except CompanyServiceError:
        db.session.rollback()
        flash("Yritystä ei löytynyt.", "warning")
        return redirect(_list_url(organization_id=organization_id))

    flash("Yritys poistettu.", "success")
    return redirect(_list_url(organization_id=organization_id))


@contacts_bp.route("", methods=["GET"])
def list_contacts():
    organization_id, redirect_response = _organization_id_for_request()
    if redirect_response is not None:
        return redirect_response

    from app.companies.models import Contact

    contacts = (
        Contact.query.filter_by(organization_id=organization_id)
        .order_by(Contact.created_at.desc())
        .all()
    )

    companies_by_id = {
        c.id: c
        for c in Company.query.filter_by(organization_id=organization_id).all()
    }

    return render_template(
        "companies/contacts.html",
        contacts=contacts,
        companies_by_id=companies_by_id,
        organization_id=organization_id,
    )


@contacts_bp.route("/new", methods=["GET", "POST"])
def new_contact():
    organization_id, redirect_response = _organization_id_for_request()
    if redirect_response is not None:
        return redirect_response

    form = ContactForm()
    _populate_contact_company_choices(form, organization_id)

    if form.validate_on_submit():
        company_id = form.company_id.data or None
        if company_id == 0:
            company_id = None
        try:
            ContactService.create_contact(
                organization_id=organization_id,
                company_id=company_id,
                first_name=form.first_name.data,
                last_name=form.last_name.data,
                email=form.email.data,
                phone=form.phone.data,
                title=form.title.data,
            )
            db.session.commit()
            flash("Kontakti luotu.", "success")
            return redirect(_contacts_list_url(organization_id=organization_id))
        except CompanyServiceError as exc:
            db.session.rollback()
            flash(exc.message, "danger")
            _populate_contact_company_choices(form, organization_id)

    return render_template(
        "companies/contact_form.html",
        form=form,
        organization_id=organization_id,
    )
