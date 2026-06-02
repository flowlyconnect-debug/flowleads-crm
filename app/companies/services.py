from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func

from app.companies.models import Company, Contact
from app.extensions import db

CLIENT_FILTER_THRESHOLD = 200

COMPANY_TYPE_LABELS = {
    "customer": "Asiakas",
    "prospect": "Prospekti",
    "partner": "Kumppani",
    "supplier": "Toimittaja",
}

COMPANY_TYPE_FILTERS = (
    ("", "Kaikki"),
    ("customer", "Asiakkaat"),
    ("prospect", "Prospektit"),
    ("partner", "Kumppanit"),
    ("supplier", "Toimittajat"),
)


class CompanyServiceError(Exception):
    def __init__(self, message: str, code: str = "company_error"):
        self.message = message
        self.code = code
        super().__init__(message)


@dataclass
class CompanyListRow:
    company: Company
    contact_count: int
    open_lead_count: int
    owner_label: str | None


def relative_created_fi(created_at: datetime | None) -> str:
    if not created_at:
        return "—"
    ts = created_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - ts
    minutes = int(max(delta.total_seconds(), 0) // 60)
    if minutes < 1:
        return "Juuri nyt"
    if minutes < 60:
        return f"{minutes} min sitten"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} h sitten"
    days = hours // 24
    if days < 7:
        return f"{days} pv sitten"
    return ts.strftime("%d.%m.%Y")


class CompanyService:
    @staticmethod
    def create_company(
        organization_id: int,
        name: str,
        *,
        type_: str = "prospect",
        industry: str | None = None,
        region: str | None = None,
        created_by: int | None = None,
    ) -> Company:
        clean_name = (name or "").strip()
        if not clean_name:
            raise CompanyServiceError("Nimi on pakollinen.", "validation")

        valid_types = {t for t, _ in COMPANY_TYPE_FILTERS if t}
        company_type = type_ if type_ in valid_types else "prospect"

        existing = (
            Company.query.filter(
                Company.organization_id == organization_id,
                func.lower(Company.name) == func.lower(clean_name),
            ).first()
        )
        if existing:
            raise CompanyServiceError("Yritys tällä nimellä on jo olemassa.", "duplicate")

        company = Company(
            organization_id=organization_id,
            name=clean_name,
            type=company_type,
            industry=(industry or "").strip() or None,
            region=(region or "").strip() or None,
            created_by=created_by,
        )
        db.session.add(company)
        return company

    @staticmethod
    def get_company_for_org(company_id: int, organization_id: int) -> Company:
        company = (
            Company.query.filter_by(id=company_id, organization_id=organization_id)
            .first()
        )
        if not company:
            raise CompanyServiceError("Company not found.", "not_found")
        return company

    @staticmethod
    def count_open_leads(company_id: int, organization_id: int) -> int:
        # "Open only — not Closed Lost"
        from app.leads.models import Lead

        return (
            Lead.query.filter_by(organization_id=organization_id, company_id=company_id)
            .filter(Lead.status.in_(("active", "won")))
            .count()
        )

    @staticmethod
    def list_open_leads(company_id: int, organization_id: int) -> list["Lead"]:
        from app.leads.models import Lead

        return (
            Lead.query.filter_by(organization_id=organization_id, company_id=company_id)
            .filter(Lead.status.in_(("active", "won")))
            .order_by(Lead.created_at.desc())
            .all()
        )

    @staticmethod
    def get_contact_for_org(contact_id: int, organization_id: int) -> Contact:
        contact = (
            Contact.query.filter_by(id=contact_id, organization_id=organization_id).first()
        )
        if not contact:
            raise CompanyServiceError("Contact not found.", "not_found")
        return contact

    @staticmethod
    def list_companies_for_index(
        organization_id: int,
        *,
        type_filter: str | None = None,
        search: str | None = None,
    ) -> tuple[list[CompanyListRow], int]:
        from app.leads.models import Lead
        from app.users.models import User

        base = Company.query.filter_by(organization_id=organization_id)
        total_count = base.count()

        query = base
        if type_filter:
            query = query.filter(Company.type == type_filter)
        if search:
            query = query.filter(Company.name.ilike(f"%{search.strip()}%"))
        companies = query.order_by(Company.created_at.desc()).all()

        if not companies:
            return [], total_count

        company_ids = [c.id for c in companies]

        contact_counts = dict(
            db.session.query(Contact.company_id, func.count(Contact.id))
            .filter(
                Contact.organization_id == organization_id,
                Contact.company_id.in_(company_ids),
            )
            .group_by(Contact.company_id)
            .all()
        )

        open_lead_counts = dict(
            db.session.query(Lead.company_id, func.count(Lead.id))
            .filter(
                Lead.organization_id == organization_id,
                Lead.company_id.in_(company_ids),
                Lead.status.in_(("active", "won")),
            )
            .group_by(Lead.company_id)
            .all()
        )

        creator_ids = {c.created_by for c in companies if c.created_by}
        users_by_id = (
            {u.id: u for u in User.query.filter(User.id.in_(creator_ids)).all()}
            if creator_ids
            else {}
        )

        owner_by_company: dict[int, str] = {}
        for company_id in company_ids:
            lead = (
                Lead.query.filter_by(organization_id=organization_id, company_id=company_id)
                .filter(Lead.status.in_(("active", "won")), Lead.assigned_to.isnot(None))
                .order_by(Lead.updated_at.desc())
                .first()
            )
            if lead and lead.assigned_to:
                user = db.session.get(User, lead.assigned_to)
                if user:
                    owner_by_company[company_id] = user.email.split("@")[0]

        rows: list[CompanyListRow] = []
        for company in companies:
            owner_label = owner_by_company.get(company.id)
            if not owner_label and company.created_by:
                creator = users_by_id.get(company.created_by)
                if creator:
                    owner_label = creator.email.split("@")[0]
            rows.append(
                CompanyListRow(
                    company=company,
                    contact_count=contact_counts.get(company.id, 0),
                    open_lead_count=open_lead_counts.get(company.id, 0),
                    owner_label=owner_label,
                )
            )
        return rows, total_count

    @staticmethod
    def delete_company(company_id: int, organization_id: int) -> None:
        from app.leads.models import Lead

        company = CompanyService.get_company_for_org(company_id, organization_id)
        Lead.query.filter_by(company_id=company.id).update(
            {Lead.company_id: None}, synchronize_session=False
        )
        Contact.query.filter_by(company_id=company.id).update(
            {Contact.company_id: None}, synchronize_session=False
        )
        db.session.delete(company)


class ContactService:
    @staticmethod
    def create_contact(
        *,
        organization_id: int,
        company_id: int | None,
        first_name: str,
        last_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        title: str | None = None,
        linkedin_url: str | None = None,
        notes: str | None = None,
        tags: list | None = None,
    ) -> Contact:
        clean_first = (first_name or "").strip()
        if not clean_first:
            raise CompanyServiceError("Etunimi on pakollinen.", "validation")
        if company_id is not None:
            CompanyService.get_company_for_org(company_id, organization_id)

        contact = Contact(
            organization_id=organization_id,
            company_id=company_id,
            first_name=clean_first,
            last_name=last_name,
            email=email,
            phone=phone,
            title=title,
            linkedin_url=linkedin_url,
            notes=notes,
            tags=tags or [],
        )
        db.session.add(contact)
        return contact

    @staticmethod
    def link_contact_to_lead(contact: Contact, lead: "Lead") -> None:
        # Defensive org check: avoid accidental cross-tenant associations
        if contact.organization_id != lead.organization_id:
            raise CompanyServiceError(
                "Cannot link contact to lead across organizations.", "forbidden"
            )
        if lead not in contact.leads:
            contact.leads.append(lead)

