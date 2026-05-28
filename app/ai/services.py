import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from app.ai.prompts import build_enrichment_prompt
from app.extensions import db
from app.leads.models import Activity, Lead
from app.leads.services import LeadService

logger = logging.getLogger(__name__)

ALLOWED_BUSINESS_MODELS = frozenset({"b2b", "b2c", "both", "unknown"})
ALLOWED_SENIORITY = frozenset(
    {"c-level", "vp", "director", "manager", "individual", "unknown"}
)
MAX_SUMMARY_LENGTH = 2000
JSON_PARSE_RETRIES = 2


def safe_error_message(exc: Exception) -> str:
    """Return a short, user-safe error message without secrets or full traces."""
    text = str(exc).strip()
    if not text:
        return "AI enrichment failed."
    text = re.sub(r"sk-[a-zA-Z0-9_-]+", "[redacted]", text)
    if "api key" in text.lower() or "authentication" in text.lower():
        return "OpenAI authentication failed. Check API key configuration."
    if "rate limit" in text.lower() or "429" in text:
        return "OpenAI rate limit reached. Try again later."
    if "timeout" in text.lower() or "timed out" in text.lower():
        return "OpenAI request timed out."
    if len(text) > 300:
        text = text[:297] + "..."
    return text


def _string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array.")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} items must be strings.")
        result.append(item.strip())
    return result


def validate_enrichment_response(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Response must be a JSON object.")

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary is required.")
    summary = summary.strip()
    if len(summary) > MAX_SUMMARY_LENGTH:
        summary = summary[:MAX_SUMMARY_LENGTH]

    company_info = data.get("company_info")
    if not isinstance(company_info, dict):
        raise ValueError("company_info must be an object.")

    business_model = company_info.get("business_model", "unknown")
    if business_model not in ALLOWED_BUSINESS_MODELS:
        raise ValueError("Invalid business_model value.")

    normalized_company = {
        "industry": str(company_info.get("industry", "")).strip(),
        "company_size_estimate": str(company_info.get("company_size_estimate", "")).strip(),
        "business_model": business_model,
        "likely_pain_points": _string_list(
            company_info.get("likely_pain_points", []), "likely_pain_points"
        ),
        "tech_stack_hints": _string_list(
            company_info.get("tech_stack_hints", []), "tech_stack_hints"
        ),
    }

    contact_info = data.get("contact_info")
    if not isinstance(contact_info, dict):
        raise ValueError("contact_info must be an object.")

    seniority = contact_info.get("seniority_level", "unknown")
    if seniority not in ALLOWED_SENIORITY:
        raise ValueError("Invalid seniority_level value.")

    likely_dm = contact_info.get("likely_decision_maker")
    if not isinstance(likely_dm, bool):
        raise ValueError("likely_decision_maker must be a boolean.")

    normalized_contact = {
        "seniority_level": seniority,
        "likely_decision_maker": likely_dm,
        "best_outreach_angle": str(contact_info.get("best_outreach_angle", "")).strip(),
    }

    lead_score = data.get("lead_score")
    if isinstance(lead_score, bool) or not isinstance(lead_score, (int, float)):
        raise ValueError("lead_score must be an integer.")
    lead_score = int(lead_score)
    if lead_score < 0 or lead_score > 100:
        raise ValueError("lead_score must be between 0 and 100.")

    score_reason = data.get("score_reason")
    if not isinstance(score_reason, str) or not score_reason.strip():
        raise ValueError("score_reason is required.")

    return {
        "summary": summary,
        "company_info": normalized_company,
        "contact_info": normalized_contact,
        "lead_score": lead_score,
        "score_reason": score_reason.strip(),
    }


def call_openai_enrichment(prompt: str, model: str, api_key: str) -> tuple[dict, dict]:
    """Call OpenAI and return (parsed_json, usage_dict)."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=60.0)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You respond only with valid JSON objects. No markdown.",
            },
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    content = response.choices[0].message.content or ""
    usage = response.usage
    usage_dict = {
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "total_tokens": getattr(usage, "total_tokens", 0) or 0,
    }
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON in OpenAI response.") from exc
    return parsed, usage_dict


class AIEnrichmentService:
    def enrich_lead(self, lead_id: int, *, retry_count: int = 0) -> bool:
        """
        Run enrichment for a lead. Returns True on success, False on final failure.
        """
        lead = db.session.get(Lead, lead_id)
        if not lead:
            logger.warning("Enrichment skipped: lead %s not found.", lead_id)
            return True

        if not current_app.config.get("AI_ENRICHMENT_ENABLED"):
            lead.ai_enrichment_status = "disabled"
            lead.ai_enrichment_error = None
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
            return True

        if not _has_enrichment_fields(lead):
            lead.ai_enrichment_status = "disabled"
            lead.ai_enrichment_error = None
            try:
                db.session.commit()
            except SQLAlchemyError:
                db.session.rollback()
            return True

        api_key = current_app.config.get("OPENAI_API_KEY")
        if not api_key:
            return self._mark_failed(
                lead,
                "OpenAI API key is not configured.",
                retry_count=retry_count,
            )

        lead.ai_enrichment_status = "processing"
        lead.ai_enrichment_error = None
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to set processing status for lead %s", lead_id)
            return False

        model = current_app.config.get("AI_ENRICHMENT_MODEL", "gpt-4o-mini")
        prompt = build_enrichment_prompt(lead)

        last_error: Exception | None = None
        for attempt in range(JSON_PARSE_RETRIES + 1):
            try:
                raw, usage = call_openai_enrichment(prompt, model, api_key)
                normalized = validate_enrichment_response(raw)
                return self._save_success(lead_id, normalized, model, usage, retry_count)
            except ValueError as exc:
                last_error = exc
                logger.warning(
                    "Invalid enrichment JSON for lead %s (attempt %s): %s",
                    lead_id,
                    attempt + 1,
                    exc,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "OpenAI enrichment error for lead %s: %s",
                    lead_id,
                    type(exc).__name__,
                )
                break

        return self._mark_failed(
            lead_id,
            safe_error_message(last_error) if last_error else "AI enrichment failed.",
            retry_count=retry_count,
            log_final_activity=True,
        )

    def _save_success(
        self,
        lead_id: int,
        data: dict,
        model: str,
        usage: dict,
        retry_count: int,
    ) -> bool:
        lead = db.session.get(Lead, lead_id)
        if not lead:
            logger.warning("Lead %s deleted during enrichment.", lead_id)
            return True

        now = datetime.now(timezone.utc)
        lead.ai_enriched = True
        lead.ai_enriched_at = now
        lead.ai_summary = data["summary"]
        lead.ai_company_info = data["company_info"]
        lead.ai_contact_info = data["contact_info"]
        lead.score = data["lead_score"]
        lead.score_reason = data["score_reason"]
        lead.ai_enrichment_status = "completed"
        lead.ai_enrichment_error = None
        lead.updated_at = now

        metadata = {
            "model": model,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "retry_count": retry_count,
        }
        LeadService.log_activity(
            lead.id,
            None,
            "ai_enriched",
            content=f"AI enrichment completed. Score: {data['lead_score']}",
            metadata=metadata,
        )
        apply_score_tags(lead)
        try:
            db.session.commit()
            return True
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to save enrichment for lead %s", lead_id)
            return False

    def _mark_failed(
        self,
        lead_or_id: Lead | int,
        message: str,
        *,
        retry_count: int = 0,
        log_final_activity: bool = False,
    ) -> bool:
        lead = (
            lead_or_id
            if isinstance(lead_or_id, Lead)
            else db.session.get(Lead, lead_or_id)
        )
        if not lead:
            return True

        db.session.rollback()
        lead = db.session.get(Lead, lead.id)
        if not lead:
            return True

        lead.ai_enrichment_status = "failed"
        lead.ai_enrichment_error = message
        if log_final_activity:
            LeadService.log_activity(
                lead.id,
                None,
                "ai_enriched",
                content=f"AI enrichment failed: {message}",
                metadata={"retry_count": retry_count, "failed": True},
            )
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Failed to mark enrichment failed for lead %s", lead.id)
        return False


def _has_enrichment_fields(lead: Lead) -> bool:
    for value in (lead.company, lead.website, lead.linkedin_url):
        if value and str(value).strip():
            return True
    return False


def apply_score_tags(lead: Lead) -> None:
    if lead.score is None:
        return

    tags = list(lead.tags or [])
    if lead.score >= 80:
        if "hot" not in tags:
            tags.append("hot")
        db.session.add(
            Activity(
                organization_id=lead.organization_id,
                lead_id=lead.id,
                type="ai_score",
                content=f"AI score {lead.score}/100 — merkitty kuumaksi liidiksi",
            )
        )
    elif lead.score >= 60:
        if "warm" not in tags:
            tags.append("warm")
        lead.tags = tags
    elif lead.score < 30:
        if "cold" not in tags:
            tags.append("cold")
    lead.tags = tags


# Backward-compatible alias for older imports/tests.
def apply_score_routing(lead: Lead, organization_id: int) -> None:
    del organization_id
    apply_score_tags(lead)
