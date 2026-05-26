import csv
import io
from datetime import date

from flask import Response

from app.analytics.services import AnalyticsService

CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _sanitize_cell(value) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in CSV_FORMULA_PREFIXES:
        return "'" + text
    return text


def _rows_to_csv(headers: list[str], rows: list[list]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_sanitize_cell(cell) for cell in row])
    return buffer.getvalue()


def export_report_csv(
    report_type: str,
    organization_id: int,
    start_date,
    end_date,
) -> Response:
    filename = f"flowleads-{report_type}-report-{date.today().isoformat()}.csv"

    if report_type == "pipeline":
        data = AnalyticsService.get_pipeline_report(organization_id, start_date, end_date)
        headers = ["stage", "lead_count", "avg_score", "conversion_from_previous_pct"]
        rows = [
            [
                s["stage_name"],
                s["lead_count"],
                s["avg_score"] if s["avg_score"] is not None else "",
                s["conversion_from_previous"]
                if s["conversion_from_previous"] is not None
                else "",
            ]
            for s in data["stages"]
        ]
        rows.append([])
        rows.append(["won", data["won"]])
        rows.append(["lost", data["lost"]])
        rows.append(["conversion_rate_pct", data["conversion_rate"]])
    elif report_type == "source":
        data = AnalyticsService.get_source_report(organization_id, start_date, end_date)
        headers = [
            "source",
            "total",
            "won",
            "lost",
            "conversion_rate_pct",
            "avg_score",
            "no_score",
        ]
        rows = [
            [
                s["source"],
                s["total"],
                s["won"],
                s["lost"],
                s["conversion_rate"],
                s["avg_score"] if s["avg_score"] is not None else "",
                s["no_score"],
            ]
            for s in data["sources"]
        ]
    elif report_type == "team":
        data = AnalyticsService.get_team_report(organization_id, start_date, end_date)
        headers = [
            "email",
            "leads_assigned",
            "notes",
            "emails_sent",
            "stage_changes",
            "calls",
            "total_activities",
        ]
        rows = [
            [
                m["email"],
                m["leads_assigned"],
                m["notes"],
                m["emails_sent"],
                m["stage_changes"],
                m["calls"],
                m["total_activities"],
            ]
            for m in data["members"]
        ]
    elif report_type == "ai":
        data = AnalyticsService.get_ai_report(organization_id, start_date, end_date)
        headers = ["metric", "value"]
        rows = [
            ["attempts", data["attempts"]],
            ["completed", data["completed"]],
            ["failed", data["failed"]],
            ["success_rate_pct", data["success_rate"]],
            ["avg_score_enriched", data["avg_score_enriched"] or ""],
            ["total_tokens", data["total_tokens"]],
            ["cost_estimate_usd", data["cost_estimate"]],
        ]
    else:
        raise ValueError(f"Unknown report type: {report_type}")

    csv_body = _rows_to_csv(headers, rows)
    return Response(
        csv_body,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
