"""Mockable OpenAI client for prediction JSON responses."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def call_openai_prediction(prompt: str, model: str, api_key: str) -> dict:
    """Call OpenAI and return parsed JSON dict."""
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
        temperature=0.2,
    )
    content = response.choices[0].message.content or ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON in OpenAI response.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("OpenAI response must be a JSON object.")
    return parsed
