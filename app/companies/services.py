from app.companies.models import Company, Contact
from app.extensions import db


class CompanyServiceError(Exception):
    def __init__(self, message: str, code: str = "company_error"):
        self.message = message
        self.code = code
        super().__init__(message)


class CompanyService:
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
        contact = Contact(
            organization_id=organization_id,
            company_id=company_id,
            first_name=first_name,
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

