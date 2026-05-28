import re

from app.email.models import ALLOWED_TEMPLATE_VARIABLES

VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
UNKNOWN_TOKEN_PATTERN = re.compile(r"\{\{[^}]+\}\}")

FALLBACKS = {
    "first_name": "",
    "last_name": "",
    "company": "",
    "ai_summary": "",
    "task_title": "",
    "due_date": "",
    "lead_name": "",
    "lead_company": "",
    "assignee_name": "",
    "org_name": "",
    "last_lead_at": "",
    "days": "",
}


def extract_variable_names(text: str) -> set[str]:
    return set(VARIABLE_PATTERN.findall(text or ""))


def validate_template_variables(*texts: str) -> tuple[bool, str | None]:
    for text in texts:
        if not text:
            continue
        for name in extract_variable_names(text):
            if name not in ALLOWED_TEMPLATE_VARIABLES:
                return False, f"Unknown template variable: {{{{{name}}}}}"
    return True, None


def build_template_context(lead, sender_name: str) -> dict[str, str]:
    first_name = (lead.first_name or "").strip()
    return {
        "first_name": first_name or "there",
        "last_name": (lead.last_name or "").strip(),
        "company": (lead.company or "").strip(),
        "sender_name": sender_name or "",
        "ai_summary": (lead.ai_summary or "").strip(),
    }


def build_task_reminder_context(task) -> dict[str, str]:
    lead = task.lead
    assignee = task.assignee
    due = task.due_date
    due_str = due.strftime("%Y-%m-%d %H:%M") if due else ""
    return {
        "task_title": (task.title or "").strip(),
        "due_date": due_str,
        "lead_name": lead.display_name if lead else "",
        "lead_company": (lead.company or "").strip() if lead else "",
        "assignee_name": (assignee.email.split("@")[0] if assignee and assignee.email else ""),
        "first_name": (assignee.email.split("@")[0] if assignee and assignee.email else "there"),
        "last_name": "",
        "company": (lead.company or "").strip() if lead else "",
        "sender_name": "FlowLeads",
        "ai_summary": "",
    }


def render_template_text(text: str, context: dict[str, str]) -> str:
    if not text:
        return ""

    def replacer(match: re.Match) -> str:
        key = match.group(1)
        if key not in ALLOWED_TEMPLATE_VARIABLES:
            return match.group(0)
        value = context.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            fallback = FALLBACKS.get(key, "")
            if key == "first_name" and not fallback:
                return "there"
            return fallback
        return str(value)

    return VARIABLE_PATTERN.sub(replacer, text)


def body_preview(text: str | None, max_len: int = 300) -> str:
    if not text:
        return ""
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) <= max_len:
        return plain
    return plain[: max_len - 3] + "..."
