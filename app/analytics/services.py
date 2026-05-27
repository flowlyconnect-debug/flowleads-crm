from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import current_app
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload

from app.api.models import APIKey
from app.email.models import EmailLog
from app.extensions import db
from app.leads.models import LEAD_SOURCES, Activity, Lead, PipelineStage
from app.users.models import Organization, User

CONTACT_ACTIVITY_TYPES = ("note", "email_sent", "call", "stage_changed")
SCORE_BUCKETS = (
    ("0-40", 0, 40),
    ("41-70", 41, 70),
    ("71-100", 71, 100),
)
KNOWN_SOURCES = set(LEAD_SOURCES)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _month_start(dt: datetime | None = None) -> datetime:
    now = dt or _utc_now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _previous_month_start(end_exclusive: datetime) -> datetime:
    first = end_exclusive.replace(day=1)
    last_day_prev = first - timedelta(days=1)
    return last_day_prev.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def pct_change(current: int, previous: int) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 1)


def conversion_rate(won: int, closed: int) -> float:
    if closed <= 0:
        return 0.0
    return round((won / closed) * 100, 1)


_FI_MONTH_SHORT = (
    "tammi",
    "helmi",
    "maalis",
    "huhti",
    "touko",
    "kesä",
    "heinä",
    "elo",
    "syys",
    "loka",
    "marras",
    "joulu",
)

_PIPELINE_DONUT_COLORS = (
    "#1D6BF3",
    "#38BDF8",
    "#7C3AED",
    "#F59E0B",
    "#10B981",
    "#EF4444",
)

_LOSS_REASON_CATEGORIES = (
    ("Kiireettömyys", ("kiire", "ei kiire", "not urgent", "no hurry")),
    ("Hintaliian korkea", ("hinta", "kallis", "expensive", "price")),
    ("Parempi vaihtoehto", ("vaihtoehto", "kilpailija", "competitor", "alternative")),
    ("Feature puuttuu", ("feature", "ominaisuus", "puuttuu", "missing")),
    ("Budjettirajoitus", ("budjetti", "budget", "rahoitus")),
)

_LOSS_REASON_COLORS = ("#EF4444", "#F59E0B", "#6B7280", "#8B5CF6", "#1D6BF3")


def _add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return dt.replace(year=year, month=month, day=day)


def _month_label(dt: datetime) -> str:
    return _FI_MONTH_SHORT[dt.month - 1]


def _month_range_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) - timedelta(microseconds=1)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(microseconds=1)
    return start, end


def _classify_loss_reason(text: str | None) -> str:
    if not text:
        return _LOSS_REASON_CATEGORIES[0][0]
    lower = text.lower()
    for label, keywords in _LOSS_REASON_CATEGORIES:
        if any(kw in lower for kw in keywords):
            return label
    return _LOSS_REASON_CATEGORIES[0][0]


def time_ago(dt: datetime | None) -> str:
    if dt is None:
        return ""
    now = _utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    return dt.strftime("%Y-%m-%d")


def _lead_base(organization_id: int):
    return Lead.query.filter(Lead.organization_id == organization_id)


def _activity_base(organization_id: int):
    return Activity.query.filter(Activity.organization_id == organization_id)


def _normalize_source(source: str | None) -> str:
    if source in KNOWN_SOURCES:
        return source
    return "other"


def _estimate_ai_cost(total_tokens: int) -> float:
    rate = float(current_app.config.get("AI_TOKEN_COST_PER_1K", 0.00015))
    return round((total_tokens / 1000.0) * rate, 4)


def _sum_ai_tokens(organization_id: int, start: datetime | None, end: datetime | None) -> int:
    q = _activity_base(organization_id).filter(Activity.type == "ai_enriched")
    if start is not None:
        q = q.filter(Activity.created_at >= start)
    if end is not None:
        q = q.filter(Activity.created_at <= end)
    total = 0
    for row in q:
        meta = row.metadata_json or {}
        if meta.get("failed"):
            continue
        total += int(meta.get("total_tokens") or 0)
    return total


class AnalyticsService:
    @staticmethod
    def get_dashboard_stats(organization_id: int, period_days: int = 30) -> dict:
        now = _utc_now()
        month_start = _month_start(now)
        prev_month_start = _previous_month_start(month_start)
        period_start = (now - timedelta(days=period_days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        total_leads = _lead_base(organization_id).count()
        leads_this_month = _lead_base(organization_id).filter(Lead.created_at >= month_start).count()
        leads_last_month = (
            _lead_base(organization_id)
            .filter(Lead.created_at >= prev_month_start, Lead.created_at < month_start)
            .count()
        )

        won = _lead_base(organization_id).filter(Lead.status == "won").count()
        lost = _lead_base(organization_id).filter(Lead.status == "lost").count()
        closed = won + lost

        avg_row = (
            db.session.query(func.avg(Lead.score))
            .filter(Lead.organization_id == organization_id, Lead.score.isnot(None))
            .scalar()
        )
        avg_score = round(float(avg_row), 1) if avg_row is not None else None

        emails_this_month = (
            EmailLog.query.filter(
                EmailLog.organization_id == organization_id,
                EmailLog.status.in_(("sent", "delivered")),
                or_(
                    and_(EmailLog.sent_at.isnot(None), EmailLog.sent_at >= month_start),
                    and_(
                        EmailLog.sent_at.is_(None),
                        EmailLog.created_at >= month_start,
                    ),
                ),
            ).count()
        )

        ai_completed = _lead_base(organization_id).filter(
            Lead.ai_enrichment_status == "completed"
        ).count()
        ai_completed_month = _lead_base(organization_id).filter(
            Lead.ai_enrichment_status == "completed",
            Lead.ai_enriched_at.isnot(None),
            Lead.ai_enriched_at >= month_start,
        ).count()

        stages = (
            PipelineStage.query.filter_by(organization_id=organization_id)
            .order_by(PipelineStage.order_index)
            .all()
        )
        stage_counts = dict(
            db.session.query(Lead.stage_id, func.count(Lead.id))
            .filter(Lead.organization_id == organization_id)
            .group_by(Lead.stage_id)
            .all()
        )
        leads_by_stage = [
            {
                "stage_id": s.id,
                "name": s.name,
                "color": s.color,
                "order_index": s.order_index,
                "count": stage_counts.get(s.id, 0),
            }
            for s in stages
        ]

        day_rows = (
            db.session.query(
                func.date(Lead.created_at),
                func.count(Lead.id),
            )
            .filter(
                Lead.organization_id == organization_id,
                Lead.created_at >= period_start,
            )
            .group_by(func.date(Lead.created_at))
            .all()
        )
        counts_by_day = {str(d): c for d, c in day_rows if d is not None}
        leads_per_day = []
        cursor = period_start.date()
        end_date = now.date()
        while cursor <= end_date:
            key = cursor.isoformat()
            leads_per_day.append({"date": key, "count": counts_by_day.get(key, 0)})
            cursor += timedelta(days=1)

        source_rows = (
            db.session.query(Lead.source, func.count(Lead.id))
            .filter(Lead.organization_id == organization_id)
            .group_by(Lead.source)
            .all()
        )
        source_totals = defaultdict(int)
        for src, cnt in source_rows:
            source_totals[_normalize_source(src)] += cnt
        for src in LEAD_SOURCES:
            source_totals.setdefault(src, 0)
        source_totals.setdefault("other", 0)
        sources_chart = [
            {"source": k, "count": source_totals[k]}
            for k in ("n8n", "manual", "import", "other")
            if source_totals[k] > 0 or k in LEAD_SOURCES
        ]
        if not any(s["count"] for s in sources_chart):
            sources_chart = [{"source": s, "count": 0} for s in LEAD_SOURCES]

        team_start = now - timedelta(days=29)
        team_rows = (
            db.session.query(Activity.user_id, func.count(Activity.id))
            .filter(
                Activity.organization_id == organization_id,
                Activity.type.in_(CONTACT_ACTIVITY_TYPES),
                Activity.created_at >= team_start.replace(hour=0, minute=0, second=0),
                Activity.user_id.isnot(None),
            )
            .group_by(Activity.user_id)
            .order_by(func.count(Activity.id).desc())
            .limit(5)
            .all()
        )
        user_ids = [uid for uid, _ in team_rows]
        users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
        top_team = [
            {
                "user_id": uid,
                "label": users[uid].email if uid in users else f"User #{uid}",
                "count": cnt,
            }
            for uid, cnt in team_rows
        ]

        from app.proposals.services import ProposalService

        open_proposals_count = ProposalService.get_open_count(organization_id)
        accepted_proposals_month = ProposalService.get_accepted_this_month_total(organization_id)

        return {
            "total_leads": total_leads,
            "open_proposals_count": open_proposals_count,
            "accepted_proposals_month": str(accepted_proposals_month),
            "leads_this_month": leads_this_month,
            "leads_last_month": leads_last_month,
            "leads_month_pct_change": pct_change(leads_this_month, leads_last_month),
            "leads_by_stage": leads_by_stage,
            "conversion_rate": conversion_rate(won, closed),
            "won_count": won,
            "lost_count": lost,
            "closed_count": closed,
            "avg_score": avg_score,
            "emails_this_month": emails_this_month,
            "ai_enrichments_completed": ai_completed,
            "ai_enrichments_completed_month": ai_completed_month,
            "charts": {
                "leads_per_day": leads_per_day,
                "stages_bar": leads_by_stage,
                "sources_pie": sources_chart,
                "top_team": top_team,
            },
        }

    @staticmethod
    def get_dashboard_extended_metrics(organization_id: int) -> dict:
        now = _utc_now()
        active_leads = _lead_base(organization_id).filter(Lead.status == "active").all()
        open_deals = len(active_leads)

        pipeline_value = Decimal("0")
        weighted_value = Decimal("0")
        age_days: list[int] = []
        for lead in active_leads:
            if lead.deal_value is not None:
                pipeline_value += lead.deal_value
            prob = lead.close_probability or Decimal("0")
            if lead.deal_value is not None:
                weighted_value += lead.deal_value * prob
            if lead.created_at:
                created = lead.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_days.append(max(0, (now - created).days))

        won_leads = (
            _lead_base(organization_id)
            .filter(Lead.status == "won")
            .all()
        )
        close_days: list[int] = []
        for lead in won_leads:
            if lead.created_at and lead.updated_at:
                created = lead.created_at
                updated = lead.updated_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                close_days.append(max(0, (updated - created).days))

        avg_days_to_close = round(sum(close_days) / len(close_days), 1) if close_days else None
        avg_deal_age = round(sum(age_days) / len(age_days), 1) if age_days else None

        month_start = _month_start(now)
        prev_month_start = _previous_month_start(month_start)
        won_this_month = sum(
            1
            for lead in won_leads
            if lead.updated_at
            and (
                lead.updated_at.replace(tzinfo=timezone.utc)
                if lead.updated_at.tzinfo is None
                else lead.updated_at
            )
            >= month_start
        )
        won_last_month = sum(
            1
            for lead in won_leads
            if lead.updated_at
            and prev_month_start
            <= (
                lead.updated_at.replace(tzinfo=timezone.utc)
                if lead.updated_at.tzinfo is None
                else lead.updated_at
            )
            < month_start
        )

        return {
            "open_deals": open_deals,
            "pipeline_value": float(pipeline_value),
            "weighted_value": float(weighted_value),
            "avg_days_to_close": avg_days_to_close,
            "avg_deal_age": avg_deal_age,
            "won_month_pct_change": pct_change(won_this_month, won_last_month),
        }

    @staticmethod
    def get_won_deals_monthly_chart(organization_id: int, months: int = 12) -> dict:
        now = _utc_now()
        labels: list[str] = []
        closed_values: list[float] = []
        won_counts: list[int] = []

        for offset in range(months - 1, -1, -1):
            month_dt = _add_months(_month_start(now), -offset)
            start, end = _month_range_bounds(month_dt.year, month_dt.month)
            rows = (
                _lead_base(organization_id)
                .filter(
                    Lead.status == "won",
                    Lead.updated_at >= start,
                    Lead.updated_at <= end,
                )
                .all()
            )
            labels.append(_month_label(month_dt))
            closed_values.append(
                float(sum((lead.deal_value or 0) for lead in rows))
            )
            won_counts.append(len(rows))

        return {
            "labels": labels,
            "closed_values": closed_values,
            "won_counts": won_counts,
        }

    @staticmethod
    def get_sales_projection_chart(organization_id: int, months: int = 12) -> dict:
        now = _utc_now()
        historical = AnalyticsService.get_won_deals_monthly_chart(organization_id, months=6)
        hist_vals = historical["closed_values"] or [0]
        monthly_baseline = sum(hist_vals) / len(hist_vals) if hist_vals else 0.0

        extended = AnalyticsService.get_dashboard_extended_metrics(organization_id)
        avg_close = extended.get("avg_days_to_close") or 45
        avg_close = max(7, int(avg_close))

        active_leads = (
            _lead_base(organization_id)
            .filter(Lead.status == "active")
            .all()
        )
        due_by_month: dict[str, float] = defaultdict(float)
        for lead in active_leads:
            if lead.created_at is None:
                continue
            created = lead.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            projected_close = created + timedelta(days=avg_close)
            if projected_close < now:
                projected_close = now + timedelta(days=7)
            key = f"{projected_close.year}-{projected_close.month:02d}"
            due_by_month[key] += float(lead.deal_value or 0)

        labels: list[str] = []
        forecasted: list[float] = []
        due_values: list[float] = []

        for offset in range(1, months + 1):
            month_dt = _add_months(_month_start(now), offset)
            labels.append(_month_label(month_dt))
            key = f"{month_dt.year}-{month_dt.month:02d}"
            due_values.append(round(due_by_month.get(key, 0), 2))
            growth = 1.0 + (offset * 0.02)
            forecasted.append(round(monthly_baseline * growth, 2))

        return {
            "labels": labels,
            "forecasted_values": forecasted,
            "due_values": due_values,
        }

    @staticmethod
    def get_pipeline_stages_donut(organization_id: int) -> list[dict]:
        stages = (
            PipelineStage.query.filter_by(organization_id=organization_id)
            .order_by(PipelineStage.order_index)
            .all()
        )
        stage_counts = dict(
            db.session.query(Lead.stage_id, func.count(Lead.id))
            .filter(Lead.organization_id == organization_id, Lead.status == "active")
            .group_by(Lead.stage_id)
            .all()
        )
        total = sum(stage_counts.values()) or 1
        result = []
        for idx, stage in enumerate(stages):
            count = stage_counts.get(stage.id, 0)
            if count <= 0:
                continue
            result.append(
                {
                    "name": stage.name,
                    "color": stage.color or _PIPELINE_DONUT_COLORS[idx % len(_PIPELINE_DONUT_COLORS)],
                    "count": count,
                    "percentage": round((count / total) * 100, 1),
                }
            )
        if not result and stages:
            result.append(
                {
                    "name": stages[0].name,
                    "color": stages[0].color or _PIPELINE_DONUT_COLORS[0],
                    "count": 0,
                    "percentage": 0.0,
                }
            )
        return result

    @staticmethod
    def get_loss_reasons_donut(organization_id: int) -> list[dict]:
        lost_leads = (
            _lead_base(organization_id)
            .filter(Lead.status == "lost")
            .all()
        )
        counts: dict[str, int] = {label: 0 for label, _ in _LOSS_REASON_CATEGORIES}
        for lead in lost_leads:
            label = _classify_loss_reason(lead.score_reason)
            counts[label] = counts.get(label, 0) + 1

        total = sum(counts.values()) or 1
        result = []
        for idx, (label, _) in enumerate(_LOSS_REASON_CATEGORIES):
            count = counts.get(label, 0)
            result.append(
                {
                    "name": label,
                    "color": _LOSS_REASON_COLORS[idx],
                    "count": count,
                    "percentage": round((count / total) * 100, 1) if count else 0.0,
                }
            )
        return result

    @staticmethod
    def get_recent_activity(organization_id: int, limit: int = 20) -> list[dict]:
        rows = (
            _activity_base(organization_id)
            .options(joinedload(Activity.lead), joinedload(Activity.user))
            .order_by(Activity.created_at.desc())
            .limit(limit)
            .all()
        )
        items = []
        for act in rows:
            lead = act.lead
            user = act.user
            items.append(
                {
                    "id": act.id,
                    "lead_id": act.lead_id,
                    "lead_name": lead.display_name if lead else f"Lead #{act.lead_id}",
                    "lead_company": lead.company if lead else None,
                    "type": act.type,
                    "content_preview": (act.content or "")[:120],
                    "user_label": user.email if user else "System",
                    "created_at": act.created_at.isoformat() if act.created_at else None,
                    "time_ago": time_ago(act.created_at),
                }
            )
        return items

    @staticmethod
    def get_pipeline_report(organization_id: int, start_date: datetime, end_date: datetime) -> dict:
        stages = (
            PipelineStage.query.filter_by(organization_id=organization_id)
            .order_by(PipelineStage.order_index)
            .all()
        )
        stage_leads = (
            db.session.query(
                Lead.stage_id,
                func.count(Lead.id),
                func.avg(Lead.score),
            )
            .filter(
                Lead.organization_id == organization_id,
                Lead.created_at >= start_date,
                Lead.created_at <= end_date,
            )
            .group_by(Lead.stage_id)
            .all()
        )
        stage_map = {sid: (cnt, avg) for sid, cnt, avg in stage_leads}

        transitions = AnalyticsService._stage_transitions(organization_id, start_date, end_date)
        avg_times = AnalyticsService._avg_time_in_stages(organization_id, start_date, end_date)

        stage_rows = []
        total_in_range = 0
        for stage in stages:
            cnt, avg = stage_map.get(stage.id, (0, None))
            total_in_range += cnt
            row = {
                "stage_id": stage.id,
                "stage_name": stage.name,
                "order_index": stage.order_index,
                "lead_count": cnt,
                "avg_score": round(float(avg), 1) if avg is not None else None,
                "conversion_from_previous": None,
                "avg_time_in_stage": avg_times.get(stage.id, "Insufficient data"),
            }
            stage_rows.append(row)

        for i, row in enumerate(stage_rows):
            if i == 0:
                continue
            prev = stage_rows[i - 1]
            prev_count = prev["lead_count"]
            if prev_count > 0:
                row["conversion_from_previous"] = round((row["lead_count"] / prev_count) * 100, 1)
            elif transitions:
                prev_id = prev["stage_id"]
                cur_id = row["stage_id"]
                moved = transitions.get((prev_id, cur_id), 0)
                from_prev = transitions.get("from_stage", {}).get(prev_id, 0)
                if from_prev > 0:
                    row["conversion_from_previous"] = round((moved / from_prev) * 100, 1)

        won = (
            _lead_base(organization_id)
            .filter(
                Lead.status == "won",
                Lead.updated_at >= start_date,
                Lead.updated_at <= end_date,
            )
            .count()
        )
        lost = (
            _lead_base(organization_id)
            .filter(
                Lead.status == "lost",
                Lead.updated_at >= start_date,
                Lead.updated_at <= end_date,
            )
            .count()
        )
        closed = won + lost

        reasons = []
        for lead in (
            _lead_base(organization_id)
            .filter(Lead.status.in_(("won", "lost")), Lead.score_reason.isnot(None))
            .filter(Lead.updated_at >= start_date, Lead.updated_at <= end_date)
            .limit(50)
        ):
            if lead.score_reason:
                reasons.append(
                    {
                        "lead_id": lead.id,
                        "status": lead.status,
                        "reason": lead.score_reason[:500],
                    }
                )

        return {
            "stages": stage_rows,
            "total_leads_in_range": total_in_range,
            "won": won,
            "lost": lost,
            "closed": closed,
            "conversion_rate": conversion_rate(won, closed),
            "won_lost_reasons": reasons,
            "has_stage_history": bool(transitions),
        }

    @staticmethod
    def _stage_transitions(
        organization_id: int, start_date: datetime, end_date: datetime
    ) -> dict | defaultdict:
        rows = (
            _activity_base(organization_id)
            .filter(
                Activity.type == "stage_changed",
                Activity.created_at >= start_date,
                Activity.created_at <= end_date,
            )
            .all()
        )
        pair_counts: dict[tuple[int, int], int] = defaultdict(int)
        from_stage: dict[int, int] = defaultdict(int)
        for act in rows:
            meta = act.metadata_json or {}
            old_id = meta.get("old_stage_id") or meta.get("old_stage")
            new_id = meta.get("new_stage_id") or meta.get("new_stage")
            if old_id is None or new_id is None:
                continue
            try:
                old_id = int(old_id)
                new_id = int(new_id)
            except (TypeError, ValueError):
                continue
            pair_counts[(old_id, new_id)] += 1
            from_stage[old_id] += 1
        if not pair_counts:
            return {}
        result = dict(pair_counts)
        result["from_stage"] = dict(from_stage)
        return result

    @staticmethod
    def _avg_time_in_stages(
        organization_id: int, start_date: datetime, end_date: datetime
    ) -> dict[int, str | float]:
        rows = (
            _activity_base(organization_id)
            .filter(
                Activity.type == "stage_changed",
                Activity.created_at >= start_date,
                Activity.created_at <= end_date,
            )
            .order_by(Activity.lead_id, Activity.created_at)
            .all()
        )
        if len(rows) < 2:
            return {sid: "Insufficient data" for sid in []}

        durations: dict[int, list[float]] = defaultdict(list)
        by_lead: dict[int, list[Activity]] = defaultdict(list)
        for act in rows:
            by_lead[act.lead_id].append(act)

        for acts in by_lead.values():
            for i in range(1, len(acts)):
                prev = acts[i - 1]
                cur = acts[i]
                meta = prev.metadata_json or {}
                stage_id = meta.get("new_stage_id") or meta.get("new_stage")
                if stage_id is None:
                    continue
                try:
                    stage_id = int(stage_id)
                except (TypeError, ValueError):
                    continue
                delta = (cur.created_at - prev.created_at).total_seconds()
                if delta >= 0:
                    durations[stage_id].append(delta)

        result: dict[int, str | float] = {}
        for stage_id, values in durations.items():
            if len(values) < 2:
                result[stage_id] = "Insufficient data"
            else:
                avg_seconds = sum(values) / len(values)
                result[stage_id] = round(avg_seconds / 86400, 1)
        return result

    @staticmethod
    def get_source_report(organization_id: int, start_date: datetime, end_date: datetime) -> dict:
        leads = (
            _lead_base(organization_id)
            .filter(Lead.created_at >= start_date, Lead.created_at <= end_date)
            .all()
        )
        by_source: dict[str, dict] = defaultdict(
            lambda: {
                "total": 0,
                "won": 0,
                "lost": 0,
                "scores": [],
                "buckets": {b[0]: 0 for b in SCORE_BUCKETS},
                "no_score": 0,
            }
        )
        for lead in leads:
            src = _normalize_source(lead.source)
            bucket = by_source[src]
            bucket["total"] += 1
            if lead.status == "won":
                bucket["won"] += 1
            elif lead.status == "lost":
                bucket["lost"] += 1
            if lead.score is None:
                bucket["no_score"] += 1
            else:
                bucket["scores"].append(lead.score)
                placed = False
                for name, lo, hi in SCORE_BUCKETS:
                    if lo <= lead.score <= hi:
                        bucket["buckets"][name] += 1
                        placed = True
                        break
                if not placed:
                    bucket["no_score"] += 1

        sources = []
        for src in ("n8n", "manual", "import", "other"):
            data = by_source.get(src)
            if not data or data["total"] == 0:
                continue
            closed = data["won"] + data["lost"]
            sources.append(
                {
                    "source": src,
                    "total": data["total"],
                    "won": data["won"],
                    "lost": data["lost"],
                    "conversion_rate": conversion_rate(data["won"], closed),
                    "score_buckets": dict(data["buckets"]),
                    "no_score": data["no_score"],
                    "avg_score": round(sum(data["scores"]) / len(data["scores"]), 1)
                    if data["scores"]
                    else None,
                }
            )

        return {"sources": sources, "total_leads": len(leads)}

    @staticmethod
    def get_team_report(organization_id: int, start_date: datetime, end_date: datetime) -> dict:
        users = User.query.filter_by(organization_id=organization_id, is_active=True).all()
        user_map = {u.id: u for u in users}

        assigned = dict(
            db.session.query(Lead.assigned_to, func.count(Lead.id))
            .filter(
                Lead.organization_id == organization_id,
                Lead.assigned_to.isnot(None),
                Lead.created_at >= start_date,
                Lead.created_at <= end_date,
            )
            .group_by(Lead.assigned_to)
            .all()
        )

        activity_counts = (
            db.session.query(
                Activity.user_id,
                Activity.type,
                func.count(Activity.id),
            )
            .filter(
                Activity.organization_id == organization_id,
                Activity.created_at >= start_date,
                Activity.created_at <= end_date,
                Activity.user_id.isnot(None),
            )
            .group_by(Activity.user_id, Activity.type)
            .all()
        )
        per_user: dict[int, dict] = defaultdict(
            lambda: {
                "notes": 0,
                "emails_sent": 0,
                "stage_changes": 0,
                "calls": 0,
                "total_activities": 0,
            }
        )
        for uid, atype, cnt in activity_counts:
            row = per_user[uid]
            row["total_activities"] += cnt
            if atype == "note":
                row["notes"] = cnt
            elif atype == "email_sent":
                row["emails_sent"] = cnt
            elif atype == "stage_changed":
                row["stage_changes"] = cnt
            elif atype == "call":
                row["calls"] = cnt

        all_user_ids = set(user_map) | set(assigned) | set(per_user)
        members = []
        for uid in sorted(all_user_ids):
            user = user_map.get(uid)
            stats = per_user.get(uid, {})
            members.append(
                {
                    "user_id": uid,
                    "email": user.email if user else f"User #{uid}",
                    "leads_assigned": assigned.get(uid, 0),
                    "notes": stats.get("notes", 0),
                    "emails_sent": stats.get("emails_sent", 0),
                    "stage_changes": stats.get("stage_changes", 0),
                    "calls": stats.get("calls", 0),
                    "total_activities": stats.get("total_activities", 0),
                }
            )
        members.sort(key=lambda m: (-m["total_activities"], m["email"]))
        return {"members": members}

    @staticmethod
    def get_ai_report(organization_id: int, start_date: datetime, end_date: datetime) -> dict:
        base = _lead_base(organization_id).filter(
            Lead.created_at >= start_date,
            Lead.created_at <= end_date,
        )
        attempts = base.filter(
            Lead.ai_enrichment_status.in_(("pending", "processing", "completed", "failed"))
        ).count()
        completed = base.filter(Lead.ai_enrichment_status == "completed").count()
        failed = base.filter(Lead.ai_enrichment_status == "failed").count()
        finished = completed + failed

        avg_score_row = (
            db.session.query(func.avg(Lead.score))
            .filter(
                Lead.organization_id == organization_id,
                Lead.ai_enrichment_status == "completed",
                Lead.score.isnot(None),
                Lead.created_at >= start_date,
                Lead.created_at <= end_date,
            )
            .scalar()
        )

        total_tokens = _sum_ai_tokens(organization_id, start_date, end_date)
        cost_estimate = _estimate_ai_cost(total_tokens)

        return {
            "attempts": attempts,
            "completed": completed,
            "failed": failed,
            "success_rate": conversion_rate(completed, finished),
            "avg_score_enriched": round(float(avg_score_row), 1) if avg_score_row else None,
            "total_tokens": total_tokens,
            "cost_estimate": cost_estimate,
            "cost_is_estimate": True,
        }

    @staticmethod
    def get_system_report(start_date: datetime | None = None, end_date: datetime | None = None) -> dict:
        now = _utc_now()
        month_start = _month_start(now)
        if start_date is None:
            start_date = month_start
        if end_date is None:
            end_date = now

        total_orgs = Organization.query.count()
        active_orgs = Organization.query.filter_by(is_active=True).count()
        total_leads = Lead.query.count()

        api_usage = []
        for key in APIKey.query.options(joinedload(APIKey.organization)).order_by(
            APIKey.request_count.desc()
        ):
            org = key.organization
            api_usage.append(
                {
                    "organization_id": key.organization_id,
                    "organization_name": org.name if org else f"Org #{key.organization_id}",
                    "key_name": key.name,
                    "key_prefix": key.key_prefix,
                    "request_count": key.request_count or 0,
                    "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
                    "is_active": key.is_usable(),
                }
            )

        ai_by_org = []
        orgs = Organization.query.order_by(Organization.name).all()
        for org in orgs:
            tokens = _sum_ai_tokens(org.id, start_date, end_date)
            ai_by_org.append(
                {
                    "organization_id": org.id,
                    "organization_name": org.name,
                    "total_tokens": tokens,
                    "cost_estimate": _estimate_ai_cost(tokens),
                }
            )

        mau_ids = set()
        for row in db.session.query(Lead.organization_id).filter(Lead.created_at >= month_start):
            mau_ids.add(row[0])
        for row in db.session.query(APIKey.organization_id).filter(APIKey.last_used_at >= month_start):
            mau_ids.add(row[0])
        for row in db.session.query(EmailLog.organization_id).filter(
            or_(
                and_(EmailLog.sent_at.isnot(None), EmailLog.sent_at >= month_start),
                and_(EmailLog.sent_at.is_(None), EmailLog.created_at >= month_start),
            )
        ):
            mau_ids.add(row[0])
        for row in db.session.query(Activity.organization_id).filter(Activity.created_at >= month_start):
            mau_ids.add(row[0])

        return {
            "total_organizations": total_orgs,
            "active_organizations": active_orgs,
            "total_leads": total_leads,
            "api_usage": api_usage,
            "ai_cost_by_organization": ai_by_org,
            "monthly_active_organizations": len(mau_ids),
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
        }
