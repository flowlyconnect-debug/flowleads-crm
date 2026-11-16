"""What a customer sees: no internal tool names in lead forms (first customer, 11/2026)."""

from app.leads.forms import LeadFilterForm, LeadForm


def test_lead_source_choices_use_customer_labels(app):
    with app.test_request_context():
        for form in (LeadForm(meta={"csrf": False}), LeadFilterForm(meta={"csrf": False})):
            labels = [label for _, label in form.source.choices]
            assert "n8n" not in labels and "Oikotie" not in labels
            assert "Automaatio" in labels and "Kohteet" in labels
