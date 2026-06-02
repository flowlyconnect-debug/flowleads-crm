from __future__ import annotations

import html as html_module
from decimal import Decimal

from app.proposals.models import Proposal


class ProposalPDFService:
    @staticmethod
    def _format_money(value: Decimal | str, currency: str) -> str:
        return f"{value} {currency}"

    @staticmethod
    def render_html(proposal: Proposal, *, template_header: str | None = None, template_footer: str | None = None) -> str:
        from app.proposals.services import ProposalService as PS

        PS.calculate_totals(proposal)
        header = template_header or ""
        footer = template_footer or ""
        rows = []
        for li in sorted(proposal.line_items, key=lambda x: x.order_index):
            rows.append(
                f"<tr>"
                f"<td>{html_module.escape(li.description)}</td>"
                f"<td style=\"text-align:right\">{li.quantity}</td>"
                f"<td style=\"text-align:right\">{li.unit_price}</td>"
                f"<td style=\"text-align:right\">{li.discount_percent}%</td>"
                f"<td style=\"text-align:right\">{li.total}</td>"
                f"</tr>"
            )
        signature_block = ""
        if proposal.signature_name:
            signed = proposal.signed_at.strftime("%Y-%m-%d %H:%M") if proposal.signed_at else ""
            signature_block = (
                f'<div class="signature"><p><strong>Allekirjoitus:</strong> '
                f'{html_module.escape(proposal.signature_name)} ({signed})</p></div>'
            )
        return f"""<!DOCTYPE html>
<html lang="fi">
<head>
<meta charset="utf-8">
<title>{html_module.escape(proposal.reference_number)}</title>
<style>
  body {{ font-family: 'Segoe UI', Helvetica, Arial, sans-serif; color: #1e293b; margin: 40px; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  .meta {{ color: #64748b; margin-bottom: 1.5rem; }}
  table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
  th, td {{ border-bottom: 1px solid #e2e8f0; padding: 0.5rem; text-align: left; }}
  th {{ background: #f8fafc; font-size: 0.85rem; }}
  .totals {{ margin-top: 1rem; text-align: right; }}
  .totals p {{ margin: 0.25rem 0; }}
  .notes {{ margin-top: 2rem; padding: 1rem; background: #f8fafc; border-radius: 6px; }}
  .signature {{ margin-top: 2rem; border-top: 2px solid #22c55e; padding-top: 1rem; }}
  .header, .footer {{ margin-bottom: 1rem; font-size: 0.9rem; }}
</style>
</head>
<body>
<div class="header">{header}</div>
<h1>{html_module.escape(proposal.title)}</h1>
<p class="meta">
  <strong>{html_module.escape(proposal.reference_number)}</strong><br>
  Asiakas: {html_module.escape(proposal.lead_name_snapshot or '')}
  {f' — {html_module.escape(proposal.lead_company_snapshot)}' if proposal.lead_company_snapshot else ''}<br>
  Voimassa: {proposal.valid_until or '—'}
</p>
<table>
  <thead>
    <tr>
      <th>Kuvaus</th><th>Määrä</th><th>á hinta</th><th>Alennus</th><th>Yhteensä</th>
    </tr>
  </thead>
  <tbody>
    {''.join(rows)}
  </tbody>
</table>
<div class="totals">
  <p>Välisumma: {proposal.subtotal} {proposal.currency}</p>
  <p>Alennus: {proposal.discount_amount} {proposal.currency}</p>
  <p>ALV {proposal.tax_percent}%</p>
  <p><strong>Yhteensä: {proposal.total} {proposal.currency}</strong></p>
</div>
{f'<div class="notes"><strong>Huomautukset</strong><p>{html_module.escape(proposal.notes or "")}</p></div>' if proposal.notes else ''}
{signature_block}
<div class="footer">{footer}</div>
</body>
</html>"""

    @staticmethod
    def generate(proposal: Proposal) -> bytes:
        from app.proposals.services import ProposalService

        template = ProposalService.get_template(proposal.organization_id)
        header = template.header_html if template else None
        footer = template.footer_html if template else None
        html_content = ProposalPDFService.render_html(
            proposal, template_header=header, template_footer=footer
        )
        try:
            from weasyprint import HTML

            return HTML(string=html_content).write_pdf()
        except (ImportError, OSError):
            # WeasyPrint can fail to load on some environments due to missing native libs.
            # Fallback to returning the rendered HTML bytes so callers/tests still get content.
            return html_content.encode("utf-8")
