from app.extensions import db
from app.tasks.models import OrganizationSettings


def get_organization_settings(organization_id: int) -> OrganizationSettings:
    settings = OrganizationSettings.query.filter_by(organization_id=organization_id).first()
    if settings:
        return settings
    settings = OrganizationSettings(organization_id=organization_id)
    db.session.add(settings)
    db.session.flush()
    return settings
