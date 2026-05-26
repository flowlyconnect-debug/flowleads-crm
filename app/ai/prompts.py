from app.leads.models import Lead


def build_enrichment_prompt(lead: Lead) -> str:
    return f"""You are a B2B sales intelligence assistant.
Given this lead information, provide enriched analysis.

Name: {lead.first_name or ''} {lead.last_name or ''}
Company: {lead.company or ''}
Title: {lead.title or ''}
Website: {lead.website or ''}
LinkedIn: {lead.linkedin_url or ''}

Return only valid JSON with this exact structure:
{{
  "summary": "2-3 sentence prospect summary for a salesperson",
  "company_info": {{
    "industry": "",
    "company_size_estimate": "",
    "business_model": "b2b|b2c|both|unknown",
    "likely_pain_points": ["", ""],
    "tech_stack_hints": [""]
  }},
  "contact_info": {{
    "seniority_level": "c-level|vp|director|manager|individual|unknown",
    "likely_decision_maker": true,
    "best_outreach_angle": ""
  }},
  "lead_score": 0,
  "score_reason": "Brief explanation of score"
}}"""
