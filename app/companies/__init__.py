from flask import Blueprint

companies_bp = Blueprint("companies", __name__, url_prefix="/companies")
contacts_bp = Blueprint("contacts", __name__, url_prefix="/contacts")

