"""AI Playbook — rule-based sales guidance for lead detail."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from app.email.utils import sender_display_name
from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import LeadService
from app.streams.models import OrgLeadSettings
from app.users.models import User

RISK_RECOMMENDATIONS = frozenset(
    {"Ota yhteyttä nyt", "Muistuta tarjouksesta", "Seuraa välittömästi"}
)
FOLLOWUP_RECOMMENDATIONS = frozenset({"Muistuta tarjouksesta", "Seuraa välittömästi"})

INDUSTRY_QUESTIONS = {
    "SaaS": "ohjelmistopalveluiden skaalautuminen ja asiakashankinta",
    "Rakentaminen": "projektinhallinta ja tarjousprosessien tehostaminen",
    "Teollisuus": "tuotannon digitalisaatio",
    "Kauppa": "verkkomyynnin ja asiakaskokemuksen kehittäminen",
    "Terveys": "potilas- ja potilastietojärjestelmien integraatiot",
    "Rahoitus": "sääntelyn noudattaminen ja prosessiautomaatio",
}


def _days_since_contact(lead: Lead, organization_id: int, now: datetime) -> int | None:
    row = (
        Activity.query.filter_by(lead_id=lead.id, organization_id=organization_id)
        .order_by(Activity.created_at.desc())
        .first()
    )
    last = row.created_at if row else lead.last_contacted_at
    if last is None:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return max(0, (now - last).days)


def _format_recommendation(lead: Lead, text: str, organization_id: int, now: datetime) -> str:
    if text == "Ota yhteyttä nyt":
        contact_days = _days_since_contact(lead, organization_id, now)
        if contact_days is not None and contact_days > 0:
            return f"Ota yhteyttä — {contact_days} pv hiljaa"
    return text


def score_label(score: int | None) -> str:
    if score is None:
        return "Ei pisteytystä — rikasta liidi AI:lla"
    if score >= 80:
        return "Korkea osuvuus — toimi nopeasti"
    if score >= 60:
        return "Hyvä liidi — seuraa aktiivisesti"
    if score >= 40:
        return "Kohtalainen — tarvitsee lisää tietoa"
    return "Heikko osuvuus — matala prioriteetti"


def score_bar_class(score: int | None) -> str:
    if score is None:
        return "playbook-score--none"
    if score >= 70:
        return "playbook-score--high"
    if score >= 40:
        return "playbook-score--mid"
    return "playbook-score--low"


def recommendation_type(text: str | None) -> str:
    if not text:
        return "opportunity"
    if text == "Lähetä tarjous":
        return "opportunity"
    if text in FOLLOWUP_RECOMMENDATIONS:
        return "followup"
    if text in RISK_RECOMMENDATIONS or "hiljaa" in text.lower():
        return "risk"
    return "opportunity"


def recommendation_pill_class(rec_type: str) -> str:
    return {
        "risk": "playbook-pill--risk",
        "followup": "playbook-pill--followup",
        "opportunity": "playbook-pill--opportunity",
    }.get(rec_type, "playbook-pill--opportunity")


def _resolve_industry(lead: Lead, organization_id: int) -> str:
    if lead.industry:
        return lead.industry
    if lead.ai_company_info and lead.ai_company_info.get("industry"):
        return str(lead.ai_company_info["industry"]).strip()
    settings = OrgLeadSettings.query.filter_by(organization_id=organization_id).first()
    if settings and settings.default_industry:
        return settings.default_industry
    return "AI-automaatio"


def _industry_question(industry: str) -> str:
    for key, question in INDUSTRY_QUESTIONS.items():
        if key.lower() in industry.lower():
            return question
    return "digitalisaatio ja prosessien tehostaminen"


def _shorten_summary(summary: str | None, max_len: int = 120) -> str:
    if not summary:
        return "yrityksenne profiili sopii hyvin palveluihimme"
    text = summary.strip()
    match = re.match(r"^(.+?[.!?])(?:\s|$)", text)
    sentence = match.group(1) if match else text
    if len(sentence) > max_len:
        return sentence[: max_len - 1].rstrip() + "…"
    return sentence


def _recommendation_email_phrase(recommendation: str | None, score: int | None) -> str:
    if recommendation == "Lähetä tarjous":
        return "lähettämään teille räätälöidyn tarjouksen"
    if recommendation and "yhteyttä" in recommendation.lower():
        return "pitämään yhteyttä ja kuulemaan ajankohtaisista tarpeistanne"
    if score is not None and score >= 70:
        return "tehostamaan myynti- ja markkinointiprosessejaan"
    return "hyödyntämään AI-automaatiota arjessa"


def _attach_ai_recommendation(lead: Lead, organization_id: int, now: datetime) -> str | None:
    from app.proposals.models import Proposal

    seven_days_ago = now - timedelta(days=7)
    has_any_proposal = (
        db.session.query(Proposal.id)
        .filter(
            Proposal.organization_id == organization_id,
            Proposal.lead_id == lead.id,
        )
        .first()
        is not None
    )
    has_old_unviewed = (
        db.session.query(Proposal.id)
        .filter(
            Proposal.organization_id == organization_id,
            Proposal.lead_id == lead.id,
            Proposal.status == "sent",
            Proposal.sent_at.isnot(None),
            Proposal.sent_at <= seven_days_ago,
        )
        .first()
        is not None
    )
    has_heavily_viewed = (
        db.session.query(Proposal.id)
        .filter(
            Proposal.organization_id == organization_id,
            Proposal.lead_id == lead.id,
            Proposal.opened_count >= 3,
        )
        .first()
        is not None
    )
    raw = LeadService._pipeline_ai_recommendation(
        lead,
        now=now,
        has_any_proposal=has_any_proposal,
        has_old_unviewed_proposal=has_old_unviewed,
        has_heavily_viewed_proposal=has_heavily_viewed,
    )
    if not raw:
        return None
    return _format_recommendation(lead, raw, organization_id, now)


def build_email_template(
    lead: Lead,
    *,
    sender_name: str,
    industry: str,
    recommendation: str | None,
) -> dict[str, str]:
    first_name = (lead.first_name or "Hei").strip()
    company = (lead.company or "yrityksenne").strip()
    subject = f"Yhteistyömahdollisuus — {company}"
    hook = _recommendation_email_phrase(recommendation, lead.score)
    body = (
        f"Hei {first_name},\n\n"
        f"Olen {sender_name} Flowly Solutionsista. Olemme erikoistuneet {industry} "
        f"ja autamme yrityksiä kuten {company} {hook}.\n\n"
        f"Voisiko teillä olla hetki jutella tällä viikolla?\n\n"
        f"Ystävällisin terveisin,\n"
        f"{sender_name}"
    )
    return {"subject": subject, "body": body}


def build_call_script(
    lead: Lead,
    *,
    sender_name: str,
    industry: str,
    ai_summary: str | None,
    recommendation: str | None,
) -> str:
    first_name = (lead.first_name or "asiakas").strip()
    company = (lead.company or "yritys").strip()
    summary_line = _shorten_summary(ai_summary)
    question = _industry_question(industry)
    return (
        f"Soittoavaus {first_name}:lle:\n\n"
        f"1. Esittely: 'Hei, olen {sender_name} Flowlylta — soitan koska {company} "
        f"nousi esiin hakuprofiilissamme.'\n"
        f"2. Avaus: '{summary_line}'\n"
        f"3. Kysymys: 'Onko {question} teillä ajankohtainen?'\n"
        f"4. CTA: 'Voisiko varata 15 min Teams-palaverin tällä viikolla?'"
    )


def _sender_name(user: User | None) -> str:
    if user is None:
        return sender_display_name()
    org = getattr(user, "organization", None)
    if org and org.email_from_name:
        return org.email_from_name
    return user.email.split("@")[0]


def get_playbook_data(lead: Lead, organization_id: int, user: User | None = None) -> dict:
    now = datetime.now(timezone.utc)
    recommendation = _attach_ai_recommendation(lead, organization_id, now)
    rec_type = recommendation_type(recommendation)
    industry = _resolve_industry(lead, organization_id)
    sender = _sender_name(user)
    email_template = build_email_template(
        lead,
        sender_name=sender,
        industry=industry,
        recommendation=recommendation,
    )
    call_script = build_call_script(
        lead,
        sender_name=sender,
        industry=industry,
        ai_summary=lead.ai_summary,
        recommendation=recommendation,
    )
    score = lead.score
    return {
        "ai_summary": lead.ai_summary or "",
        "ai_enrichment_status": lead.ai_enrichment_status,
        "ai_recommendation": recommendation or "",
        "recommendation_type": rec_type,
        "recommendation_pill_class": recommendation_pill_class(rec_type),
        "score": score,
        "score_label": score_label(score),
        "score_bar_class": score_bar_class(score),
        "email_template": email_template,
        "call_script": call_script,
        "sender_name": sender,
    }
