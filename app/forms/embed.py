from __future__ import annotations

import json

from markupsafe import escape

from app.forms.models import WebForm


def render_form_fields_html(fields: list, *, field_prefix: str = "") -> str:
    parts: list[str] = []
    prefix = f"{field_prefix}" if field_prefix else ""
    for field in fields:
        key = escape(field.get("key", ""))
        label = escape(field.get("label", key))
        field_type = field.get("type", "text")
        required = field.get("required")
        req_attr = ' required aria-required="true"' if required else ""
        req_mark = ' <span class="fl-req">*</span>' if required else ""
        name = f"{prefix}{key}" if prefix else str(key)
        field_id = f"fl-{prefix}{key}" if prefix else f"fl-{key}"

        parts.append(f'<div class="fl-field" data-field="{key}">')
        if field_type != "checkbox":
            parts.append(f'<label for="{field_id}">{label}{req_mark}</label>')

        if field_type == "textarea":
            parts.append(
                f'<textarea id="{field_id}" name="{name}" rows="4"{req_attr}></textarea>'
            )
        elif field_type == "select":
            options_html = "".join(
                f'<option value="{escape(opt)}">{escape(opt)}</option>'
                for opt in (field.get("options") or [])
            )
            parts.append(
                f'<select id="{field_id}" name="{name}"{req_attr}>'
                f'<option value="">—</option>{options_html}</select>'
            )
        elif field_type == "checkbox":
            parts.append(
                f'<label class="fl-checkbox">'
                f'<input type="checkbox" id="{field_id}" name="{name}" value="1"> '
                f"{label}{req_mark}</label>"
            )
        else:
            input_type = field_type if field_type in ("email", "tel", "number") else "text"
            parts.append(
                f'<input type="{input_type}" id="{field_id}" name="{name}"{req_attr}>'
            )

        parts.append('<span class="fl-error" role="alert"></span>')
        parts.append("</div>")
    return "\n".join(parts)


def render_iframe_page(form: WebForm, *, submit_url: str) -> str:
    title = escape(form.title)
    description = escape(form.description or "")
    button = escape(form.submit_button_text)
    fields_html = render_form_fields_html(form.fields or [])
    success_js = json.dumps(form.success_message)
    submit_js = json.dumps(submit_url)

    return f"""<!DOCTYPE html>
<html lang="fi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ font-family: system-ui, -apple-system, sans-serif; color: #0f172a; }}
    body {{ margin: 0; padding: 1.25rem; background: #f8fafc; }}
    .fl-wrap {{ max-width: 520px; margin: 0 auto; background: #fff; padding: 1.5rem; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
    h1 {{ font-size: 1.35rem; margin: 0 0 0.5rem; }}
    .fl-desc {{ color: #64748b; margin-bottom: 1.25rem; font-size: 0.95rem; }}
    .fl-field {{ margin-bottom: 1rem; }}
    label {{ display: block; font-size: 0.875rem; font-weight: 600; margin-bottom: 0.35rem; }}
    .fl-req {{ color: #dc2626; }}
    input, textarea, select {{ width: 100%; padding: 0.55rem 0.65rem; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 1rem; box-sizing: border-box; }}
    input:focus, textarea:focus, select:focus {{ outline: 2px solid #38bdf8; border-color: #38bdf8; }}
    .fl-checkbox {{ display: flex; align-items: center; gap: 0.5rem; font-weight: normal; }}
    .fl-checkbox input {{ width: auto; }}
    .fl-error {{ display: block; color: #dc2626; font-size: 0.8rem; margin-top: 0.25rem; min-height: 1rem; }}
    .fl-global-error {{ background: #fef2f2; color: #b91c1c; padding: 0.75rem; border-radius: 6px; margin-bottom: 1rem; display: none; }}
    button[type=submit] {{ width: 100%; padding: 0.7rem; background: #0ea5e9; color: #fff; border: none; border-radius: 6px; font-size: 1rem; font-weight: 600; cursor: pointer; margin-top: 0.5rem; }}
    button[type=submit]:disabled {{ opacity: 0.6; cursor: wait; }}
    .fl-success {{ text-align: center; padding: 2rem 1rem; color: #166534; }}
    .fl-hp {{ position: absolute; left: -9999px; opacity: 0; height: 0; width: 0; overflow: hidden; }}
  </style>
</head>
<body>
  <div class="fl-wrap" id="fl-form-root">
    <h1>{title}</h1>
    {"<p class='fl-desc'>" + description + "</p>" if description else ""}
    <div class="fl-global-error" id="fl-global-error" role="alert"></div>
    <form id="fl-form" novalidate>
      {fields_html}
      <div class="fl-hp" aria-hidden="true">
        <label for="fl-hp">Leave blank</label>
        <input type="text" id="fl-hp" name="_hp" tabindex="-1" autocomplete="off">
      </div>
      <button type="submit" id="fl-submit">{button}</button>
    </form>
  </div>
  <script>
  (function() {{
    const form = document.getElementById('fl-form');
    const root = document.getElementById('fl-form-root');
    const globalErr = document.getElementById('fl-global-error');
    const submitBtn = document.getElementById('fl-submit');
    const successMsg = {success_js};
    const submitUrl = {submit_js};

    function clearErrors() {{
      globalErr.style.display = 'none';
      globalErr.textContent = '';
      form.querySelectorAll('.fl-error').forEach(el => el.textContent = '');
      form.querySelectorAll('.fl-field').forEach(el => el.classList.remove('fl-invalid'));
    }}

    function showFieldErrors(fields) {{
      if (!fields) return;
      Object.entries(fields).forEach(([key, msg]) => {{
        const wrap = form.querySelector('[data-field="' + key + '"]');
        if (!wrap) return;
        wrap.classList.add('fl-invalid');
        const err = wrap.querySelector('.fl-error');
        if (err) err.textContent = msg;
      }});
    }}

    form.addEventListener('submit', async function(e) {{
      e.preventDefault();
      clearErrors();
      submitBtn.disabled = true;
      const fd = new FormData(form);
      const body = {{}};
      fd.forEach((v, k) => {{ body[k] = v; }});
      try {{
        const res = await fetch(submitUrl, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json', 'Accept': 'application/json' }},
          body: JSON.stringify(body),
        }});
        const json = await res.json();
        if (json.success) {{
          root.innerHTML = '<div class="fl-success"><p>' + (json.message || successMsg) + '</p></div>';
          return;
        }}
        const err = json.error || {{}};
        if (err.fields) showFieldErrors(err.fields);
        globalErr.textContent = err.message || 'Lähetys epäonnistui.';
        globalErr.style.display = 'block';
      }} catch (_) {{
        globalErr.textContent = 'Yhteysvirhe. Yritä uudelleen.';
        globalErr.style.display = 'block';
      }} finally {{
        submitBtn.disabled = false;
      }}
    }});
  }})();
  </script>
</body>
</html>"""
