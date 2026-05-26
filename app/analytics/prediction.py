"""Deal close probability scoring and revenue forecasting."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from flask import current_app
from sqlalchemy.orm import joinedload

from app.analytics.models import PredictionLog
from app.analytics.openai_client import call_openai_prediction
from app.analytics.services import conversion_rate
from app.calendar.models import CalendarEvent
from app.email.models import EmailLog
from app.extensions import db
from app.leads.models import Activity, Lead, PipelineStage
from app.leads.services import LeadServiceError, get_lead_for_org
from app.proposals.models import Proposal

logger = logging.getLogger(__name__)

FALLBACK_MODEL_VERSION = "fallback-v1"
OPENAI_PREDICTION_MODEL_VERSION = "openai-gpt-4.1-mini-v1"

ACTIVITY_COUNT_TYPES = {
    "notes": ("note",),
    "calls": ("call",),
    "emails": ("email_sent",),
    "tasks": ("task_created", "task_completed"),
    "proposals": (
        "proposal_sent",
        "proposal_viewed",
        "proposal_accepted",
        "proposal_declined",
    ),
    "meetings": ("meeting_scheduled",),
}

PROPOSAL_STATUS_RANK = {
    "draft": 0,
    "sent": 1,
    "viewed": 2,
    "accepted": 3,
    "declined": 4,
    "expired": 5,
}


class PredictionServiceError(Exception):
    def __init__(self, message: str, code: str = "prediction_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _clamp_probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


class PredictionService:
    @staticmethod
    def collect_signals(lead_id: int) -> dict:
        lead = db.session.get(Lead, lead_id)
        if not lead:
            raise PredictionServiceError("Lead not found.", "not_found")

        organization_id = lead.organization_id
        stage = db.session.get(PipelineStage, lead.stage_id)
        stage_name = stage.name if stage else None

        activities = Activity.query.filter_by(
            lead_id=lead_id, organization_id=organization_id
        ).all()
        activity_counts = {key: 0 for key in ACTIVITY_COUNT_TYPES}
        for act in activities:
            for key, types in ACTIVITY_COUNT_TYPES.items():
                if act.type in types:
                    activity_counts[key] += 1

        days_in_current_stage = PredictionService._days_in_current_stage(lead_id, organization_id)
        days_since_created = PredictionService._days_between(lead.created_at, _utc_now())
        days_since_last_contact = PredictionService._days_between(
            lead.last_contacted_at, _utc_now()
        )

        email_stats = PredictionService._email_engagement(lead_id, organization_id)
        proposal_info = PredictionService._proposal_signals(lead_id, organization_id)
        has_meeting = PredictionService._has_meeting(lead_id, organization_id)
        response_rate = PredictionService._response_rate(lead_id, organization_id)

        company_size = None
        industry_tag = None
        if lead.ai_company_info and isinstance(lead.ai_company_info, dict):
            company_size = lead.ai_company_info.get("company_size_estimate")
            industry_tag = lead.ai_company_info.get("industry")

        tags = list(lead.tags or [])
        org_stats = PredictionService._org_historical_stats(organization_id)

        return {
            "lead_id": lead.id,
            "organization_id": organization_id,
            "lead_score": lead.score,
            "pipeline_stage": stage_name,
            "lead_status": lead.status,
            "days_in_current_stage": days_in_current_stage,
            "days_since_created": days_since_created,
            "days_in_pipeline": days_since_created,
            "days_since_last_contact": days_since_last_contact,
            "activity_counts": activity_counts,
            "email_open_rate": email_stats.get("open_rate"),
            "email_click_rate": email_stats.get("click_rate"),
            "emails_sent": email_stats.get("sent_count"),
            "emails_opened": email_stats.get("opened_count"),
            "emails_clicked": email_stats.get("clicked_count"),
            "has_proposal": proposal_info["has_proposal"],
            "proposal_status": proposal_info["best_status"],
            "has_had_meeting": has_meeting,
            "response_rate": response_rate,
            "tags": tags,
            "industry": industry_tag,
            "company_size_estimate": company_size,
            "deal_value": _to_float(lead.deal_value),
            "expected_value": _to_float(lead.expected_value),
            "close_probability": _to_float(lead.close_probability),
            "conversion_rate_percent": org_stats["conversion_rate_percent"],
            "avg_days_to_close": org_stats["avg_days_to_close"],
        }

    @staticmethod
    def _days_between(start: datetime | None, end: datetime) -> int | None:
        if start is None:
            return None
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return max(0, (end - start).days)

    @staticmethod
    def _days_in_current_stage(lead_id: int, organization_id: int) -> int | None:
        last_change = (
            Activity.query.filter_by(
                lead_id=lead_id,
                organization_id=organization_id,
                type="stage_changed",
            )
            .order_by(Activity.created_at.desc())
            .first()
        )
        if last_change and last_change.created_at:
            return PredictionService._days_between(last_change.created_at, _utc_now())
        lead = db.session.get(Lead, lead_id)
        if lead and lead.created_at:
            return PredictionService._days_between(lead.created_at, _utc_now())
        return None

    @staticmethod
    def _email_engagement(lead_id: int, organization_id: int) -> dict:
        logs = EmailLog.query.filter_by(
            lead_id=lead_id, organization_id=organization_id
        ).all()
        sent = sum(1 for log in logs if log.status in ("sent", "delivered"))
        opened = sum(1 for log in logs if log.status == "opened")
        clicked = sum(1 for log in logs if log.status == "clicked")
        open_rate = round(opened / sent, 3) if sent else None
        click_rate = round(clicked / sent, 3) if sent else None
        return {
            "sent_count": sent,
            "opened_count": opened,
            "clicked_count": clicked,
            "open_rate": open_rate,
            "click_rate": click_rate,
        }

    @staticmethod
    def _proposal_signals(lead_id: int, organization_id: int) -> dict:
        proposals = (
            Proposal.query.filter_by(lead_id=lead_id, organization_id=organization_id)
            .order_by(Proposal.updated_at.desc())
            .all()
        )
        if not proposals:
            return {"has_proposal": False, "best_status": None}
        best = max(
            proposals,
            key=lambda p: PROPOSAL_STATUS_RANK.get(p.status, -1),
        )
        return {"has_proposal": True, "best_status": best.status}

    @staticmethod
    def _has_meeting(lead_id: int, organization_id: int) -> bool:
        if (
            CalendarEvent.query.filter_by(
                lead_id=lead_id,
                organization_id=organization_id,
                status="scheduled",
            ).first()
        ):
            return True
        return (
            Activity.query.filter_by(
                lead_id=lead_id,
                organization_id=organization_id,
                type="meeting_scheduled",
            ).first()
            is not None
        )

    @staticmethod
    def _response_rate(lead_id: int, organization_id: int) -> float | None:
        sent = Activity.query.filter_by(
            lead_id=lead_id,
            organization_id=organization_id,
            type="email_sent",
        ).count()
        if sent == 0:
            return None
        replies = Activity.query.filter(
            Activity.lead_id == lead_id,
            Activity.organization_id == organization_id,
            Activity.type.in_(("note", "call")),
            Activity.content.isnot(None),
        ).count()
        return round(min(replies / sent, 1.0), 3)

    @staticmethod
    def _org_historical_stats(organization_id: int) -> dict:
        won = Lead.query.filter_by(organization_id=organization_id, status="won").count()
        lost = Lead.query.filter_by(organization_id=organization_id, status="lost").count()
        closed = won + lost
        rate = conversion_rate(won, closed)

        won_leads = Lead.query.filter_by(
            organization_id=organization_id, status="won"
        ).all()
        durations = []
        for wl in won_leads:
            if wl.created_at and wl.updated_at:
                days = PredictionService._days_between(wl.created_at, wl.updated_at)
                if days is not None:
                    durations.append(days)
        avg_days = round(sum(durations) / len(durations), 1) if durations else None

        return {
            "conversion_rate_percent": rate,
            "avg_days_to_close": avg_days,
        }

    @staticmethod
    def predict_lead(lead_id: int) -> float:
        lead = db.session.get(Lead, lead_id)
        if not lead:
            raise PredictionServiceError("Lead not found.", "not_found")

        signals = PredictionService.collect_signals(lead_id)
        org_stats = PredictionService._org_historical_stats(lead.organization_id)

        parsed = None
        model_version = OPENAI_PREDICTION_MODEL_VERSION
        api_key = current_app.config.get("OPENAI_API_KEY")
        openai_model = current_app.config.get(
            "AI_PREDICTION_MODEL",
            current_app.config.get("AI_ENRICHMENT_MODEL", "gpt-4o-mini"),
        )

        if api_key:
            prompt = PredictionService._build_prompt(signals, org_stats)
            try:
                parsed = call_openai_prediction(prompt, openai_model, api_key)
            except Exception as exc:
                logger.warning(
                    "OpenAI prediction failed for lead %s: %s", lead_id, type(exc).__name__
                )
                parsed = None

        if parsed is None:
            probability = PredictionService._fallback_score(signals)
            model_version = FALLBACK_MODEL_VERSION
            key_positive = PredictionService._fallback_positive_signals(signals)
            key_risk = PredictionService._fallback_risk_signals(signals)
            recommendation = PredictionService._fallback_recommendation(signals)
        else:
            try:
                probability, key_positive, key_risk, recommendation = (
                    PredictionService._parse_openai_response(parsed)
                )
            except ValueError:
                logger.warning("Invalid OpenAI prediction JSON for lead %s, using fallback", lead_id)
                probability = PredictionService._fallback_score(signals)
                model_version = FALLBACK_MODEL_VERSION
                key_positive = PredictionService._fallback_positive_signals(signals)
                key_risk = PredictionService._fallback_risk_signals(signals)
                recommendation = PredictionService._fallback_recommendation(signals)

        probability = _clamp_probability(probability)
        now = _utc_now()

        lead.close_probability = Decimal(str(round(probability, 4)))
        lead.probability_updated_at = now
        if lead.deal_value is not None:
            lead.expected_value = Decimal(str(round(float(lead.deal_value) * probability, 2)))
        else:
            lead.expected_value = None
        lead.updated_at = now

        log = PredictionLog(
            lead_id=lead.id,
            organization_id=lead.organization_id,
            probability=lead.close_probability,
            signals=signals,
            model_version=model_version,
            key_positive_signals=key_positive or [],
            key_risk_signals=key_risk or [],
            recommendation=recommendation,
        )
        db.session.add(log)
        db.session.flush()
        return float(probability)

    @staticmethod
    def _build_prompt(signals: dict, org_stats: dict) -> str:
        signals_json = json.dumps(signals, ensure_ascii=False, default=str)
        conversion = org_stats.get("conversion_rate_percent", 0)
        avg_days = org_stats.get("avg_days_to_close")
        avg_days_str = str(avg_days) if avg_days is not None else "unknown"
        days_pipeline = signals.get("days_in_pipeline")
        days_pipeline_str = str(days_pipeline) if days_pipeline is not None else "unknown"

        return f"""You are a B2B sales analyst. Based on these signals, estimate the probability
that this lead will become a paying customer within 90 days.

Lead signals:
{signals_json}

Historical context for this organization:
- Average conversion rate: {conversion}%
- Average days to close: {avg_days_str}
- This lead has been in pipeline: {days_pipeline_str} days

Return ONLY a JSON object:
{{
  "probability": 0.73,
  "key_positive_signals": ["has had meeting", "email opened 3 times"],
  "key_risk_signals": ["no proposal sent yet", "no contact in 7 days"],
  "recommendation": "Send proposal this week — lead shows strong buying signals"
}}"""

    @staticmethod
    def _parse_openai_response(data: dict) -> tuple[float, list, list, str | None]:
        prob = data.get("probability")
        if prob is None:
            raise ValueError("Missing probability in OpenAI response.")
        try:
            probability = float(prob)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid probability value.") from exc

        key_positive = data.get("key_positive_signals") or []
        key_risk = data.get("key_risk_signals") or []
        if not isinstance(key_positive, list):
            key_positive = []
        if not isinstance(key_risk, list):
            key_risk = []
        key_positive = [str(x) for x in key_positive]
        key_risk = [str(x) for x in key_risk]

        recommendation = data.get("recommendation")
        if recommendation is not None:
            recommendation = str(recommendation).strip() or None

        return probability, key_positive, key_risk, recommendation

    @staticmethod
    def _fallback_score(signals: dict) -> float:
        score = 0.15

        lead_score = signals.get("lead_score")
        if lead_score is not None:
            score += (lead_score / 100.0) * 0.25

        proposal_status = signals.get("proposal_status")
        if proposal_status == "accepted":
            score += 0.35
        elif proposal_status == "viewed":
            score += 0.22
        elif proposal_status == "sent":
            score += 0.15
        elif proposal_status == "draft":
            score += 0.05

        if signals.get("has_had_meeting"):
            score += 0.12

        days_contact = signals.get("days_since_last_contact")
        if days_contact is not None:
            if days_contact <= 3:
                score += 0.1
            elif days_contact <= 7:
                score += 0.05
            elif days_contact > 14:
                score -= 0.08

        activity = signals.get("activity_counts") or {}
        total_activities = sum(activity.values())
        score += min(total_activities * 0.02, 0.12)

        open_rate = signals.get("email_open_rate")
        if open_rate is not None and open_rate > 0:
            score += min(open_rate * 0.15, 0.1)

        stage = (signals.get("pipeline_stage") or "").lower()
        if "won" in stage or signals.get("lead_status") == "won":
            score = 0.95
        elif "lost" in stage or signals.get("lead_status") == "lost":
            score = 0.05
        elif "proposal" in stage:
            score += 0.08
        elif "interested" in stage:
            score += 0.06

        response_rate = signals.get("response_rate")
        if response_rate is not None:
            score += min(response_rate * 0.1, 0.08)

        return _clamp_probability(score)

    @staticmethod
    def _fallback_positive_signals(signals: dict) -> list[str]:
        positive = []
        if signals.get("has_had_meeting"):
            positive.append("has had meeting")
        status = signals.get("proposal_status")
        if status in ("sent", "viewed", "accepted"):
            positive.append(f"proposal {status}")
        if signals.get("lead_score") and signals["lead_score"] >= 70:
            positive.append(f"high lead score ({signals['lead_score']})")
        if signals.get("days_since_last_contact") is not None and signals["days_since_last_contact"] <= 7:
            positive.append("recent contact")
        return positive

    @staticmethod
    def _fallback_risk_signals(signals: dict) -> list[str]:
        risks = []
        if not signals.get("has_proposal"):
            risks.append("no proposal yet")
        elif signals.get("proposal_status") == "draft":
            risks.append("proposal still in draft")
        if signals.get("days_since_last_contact") is not None and signals["days_since_last_contact"] > 7:
            risks.append(f"no contact in {signals['days_since_last_contact']} days")
        if not signals.get("has_had_meeting"):
            risks.append("no meeting scheduled")
        return risks

    @staticmethod
    def _fallback_recommendation(signals: dict) -> str | None:
        if signals.get("proposal_status") in (None, "draft") and signals.get("has_had_meeting"):
            return "Send proposal this week — lead shows meeting engagement"
        if signals.get("days_since_last_contact") is not None and signals["days_since_last_contact"] > 7:
            return "Re-engage lead — no recent contact"
        return "Continue nurturing based on current pipeline stage"

    @staticmethod
    def predict_batch(organization_id: int) -> dict:
        leads = Lead.query.filter(
            Lead.organization_id == organization_id,
            Lead.status == "active",
        ).all()

        processed = 0
        failed = 0
        errors: list[dict] = []

        for lead in leads:
            try:
                PredictionService.predict_lead(lead.id)
                db.session.commit()
                processed += 1
            except Exception as exc:
                db.session.rollback()
                failed += 1
                msg = str(exc)
                if isinstance(exc, PredictionServiceError):
                    msg = exc.message
                errors.append({"lead_id": lead.id, "error": msg})
                logger.exception(
                    "Batch prediction failed for lead %s in org %s",
                    lead.id,
                    organization_id,
                )

        return {"processed": processed, "failed": failed, "errors": errors}

    @staticmethod
    def calculate_forecast(organization_id: int, period_days: int = 30) -> dict:
        leads = (
            Lead.query.filter(
                Lead.organization_id == organization_id,
                Lead.status == "active",
            )
            .options(joinedload(Lead.stage))
            .all()
        )

        stages = (
            PipelineStage.query.filter_by(organization_id=organization_id)
            .order_by(PipelineStage.order_index)
            .all()
        )
        stage_names = {s.id: s.name for s in stages}
        by_stage: dict[str, dict] = {}

        expected_revenue = Decimal("0")
        best_case = Decimal("0")
        conservative_case = Decimal("0")

        for lead in leads:
            deal = lead.deal_value or Decimal("0")
            prob = float(lead.close_probability or 0)
            expected = deal * Decimal(str(prob))
            expected_revenue += expected

            if prob > 0.5:
                best_case += deal
            if prob > 0.8:
                conservative_case += deal

            stage_name = stage_names.get(lead.stage_id, "Unknown")
            if stage_name not in by_stage:
                by_stage[stage_name] = {
                    "expected_revenue": Decimal("0"),
                    "deal_value": Decimal("0"),
                    "count": 0,
                }
            by_stage[stage_name]["expected_revenue"] += expected
            by_stage[stage_name]["deal_value"] += deal
            by_stage[stage_name]["count"] += 1

        by_stage_out = {
            name: {
                "expected_revenue": float(data["expected_revenue"]),
                "deal_value": float(data["deal_value"]),
                "count": data["count"],
            }
            for name, data in by_stage.items()
        }

        return {
            "expected_revenue": float(expected_revenue),
            "best_case": float(best_case),
            "conservative_case": float(conservative_case),
            "leads_count": len(leads),
            "by_stage": by_stage_out,
            "period_days": period_days,
        }

    @staticmethod
    def get_forecast_deals(organization_id: int) -> list[dict]:
        leads = (
            Lead.query.filter(
                Lead.organization_id == organization_id,
                Lead.status == "active",
            )
            .options(joinedload(Lead.stage))
            .all()
        )
        rows = []
        for lead in leads:
            prob = float(lead.close_probability or 0)
            deal = float(lead.deal_value or 0)
            expected = deal * prob
            rows.append(
                {
                    "lead": lead,
                    "lead_id": lead.id,
                    "lead_name": lead.display_name,
                    "company": lead.company,
                    "stage_name": lead.stage.name if lead.stage else "",
                    "probability": prob,
                    "deal_value": deal,
                    "expected_value": expected,
                    "last_contacted_at": lead.last_contacted_at,
                }
            )
        rows.sort(key=lambda r: r["expected_value"], reverse=True)
        return rows

    @staticmethod
    def get_high_potential_leads(organization_id: int, limit: int = 5) -> list[dict]:
        leads = Lead.query.filter(
            Lead.organization_id == organization_id,
            Lead.status == "active",
        ).all()
        scored = []
        for lead in leads:
            prob = float(lead.close_probability or 0)
            deal = float(lead.deal_value or 0)
            scored.append(
                {
                    "lead": lead,
                    "lead_id": lead.id,
                    "lead_name": lead.display_name,
                    "company": lead.company,
                    "potential": prob * deal,
                    "probability": prob,
                    "deal_value": deal,
                }
            )
        scored.sort(key=lambda x: x["potential"], reverse=True)
        return scored[:limit]

    @staticmethod
    def get_historical_accuracy(organization_id: int) -> dict | None:
        quarter_start = _utc_now() - timedelta(days=90)
        logs = (
            PredictionLog.query.filter(
                PredictionLog.organization_id == organization_id,
                PredictionLog.created_at >= quarter_start,
            )
            .order_by(PredictionLog.created_at.desc())
            .all()
        )
        if len(logs) < 5:
            return None

        evaluated = 0
        correct = 0
        for log in logs:
            lead = db.session.get(Lead, log.lead_id)
            if not lead or lead.status not in ("won", "lost"):
                continue
            evaluated += 1
            predicted_win = float(log.probability) >= 0.5
            actual_win = lead.status == "won"
            if predicted_win == actual_win:
                correct += 1

        if evaluated < 5:
            return None

        return {
            "accuracy_percent": round((correct / evaluated) * 100, 1),
            "sample_size": evaluated,
            "period": "last_quarter",
        }

    @staticmethod
    def get_latest_prediction(lead_id: int, organization_id: int) -> PredictionLog | None:
        return (
            PredictionLog.query.filter_by(
                lead_id=lead_id, organization_id=organization_id
            )
            .order_by(PredictionLog.created_at.desc())
            .first()
        )

    @staticmethod
    def predict_lead_for_org(lead_id: int, organization_id: int) -> float:
        get_lead_for_org(lead_id, organization_id)
        return PredictionService.predict_lead(lead_id)


def run_weekly_batch_predictions(app) -> None:
    """Run batch prediction for all orgs with active leads (scheduler entry)."""
    with app.app_context():
        org_ids = (
            db.session.query(Lead.organization_id)
            .filter(Lead.status == "active")
            .distinct()
            .all()
        )
        for (org_id,) in org_ids:
            try:
                result = PredictionService.predict_batch(org_id)
                logger.info(
                    "Weekly prediction batch org %s: processed=%s failed=%s",
                    org_id,
                    result["processed"],
                    result["failed"],
                )
            except Exception:
                logger.exception("Weekly prediction batch failed for org %s", org_id)
