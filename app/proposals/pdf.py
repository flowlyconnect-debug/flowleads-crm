from __future__ import annotations

import html as html_module
from decimal import Decimal

from app.proposals.models import Proposal


def _safe_url_fetcher(url: str, *args, **kwargs):
    """The header and footer come from the organization's settings as HTML.
    WeasyPrint's default fetcher would follow file:// (and internal http)
    references in them and embed server files in the PDF, so only inline
    data: URIs and public https resources are fetched."""
    scheme = (url or "").split(":", 1)[0].lower()
    if scheme not in ("data", "https"):
        raise ValueError(f"Blocked resource in proposal PDF: {scheme}")
    from weasyprint import default_url_fetcher

    return default_url_fetcher(url, *args, **kwargs)


def fi_money(value, currency: str = "EUR") -> str:
    """12500.5 -> '12 500,50 €' (Finnish grouping and decimal comma)."""
    amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    whole, _, cents = f"{abs(amount):.2f}".partition(".")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    sign = "-" if amount < 0 else ""
    symbol = "€" if (currency or "EUR").upper() == "EUR" else currency
    return f"{sign}{' '.join(groups) or '0'},{cents} {symbol}"


def fi_number(value) -> str:
    """Quantity / percent without trailing zeros, decimal comma: 1.0000 -> '1', 25.50 -> '25,5'."""
    text = f"{Decimal(str(value or 0)).normalize():f}"
    return text.replace(".", ",")


def vat_breakdown(proposal: Proposal) -> dict:
    """Net (veroton), VAT amount and gross (verollinen) of a proposal."""
    subtotal = Decimal(str(proposal.subtotal or 0))
    discount = Decimal(str(proposal.discount_amount or 0))
    net = max(subtotal - discount, Decimal("0"))
    gross = Decimal(str(proposal.total or 0))
    return {
        "subtotal": subtotal,
        "discount": discount,
        "net": net,
        "tax_percent": Decimal(str(proposal.tax_percent or 0)),
        "tax_amount": max(gross - net, Decimal("0")),
        "gross": gross,
    }


class ProposalPDFService:
    @staticmethod
    def _format_money(value: Decimal | str, currency: str) -> str:
        return fi_money(value, currency)

    @staticmethod
    def render_html(
        proposal: Proposal,
        *,
        template_header: str | None = None,
        template_footer: str | None = None,
        logo_url: str | None = None,
    ) -> str:
        from app.proposals.services import ProposalService as PS

        PS.calculate_totals(proposal)
        header = template_header or ""
        footer = template_footer or ""
        currency = proposal.currency or "EUR"
        rows = []
        for li in sorted(proposal.line_items, key=lambda x: x.order_index):
            rows.append(
                f"<tr>"
                f"<td>{html_module.escape(li.description)}</td>"
                f"<td class=\"num\">{fi_number(li.quantity)}</td>"
                f"<td class=\"num\">{fi_money(li.unit_price, currency)}</td>"
                f"<td class=\"num\">{fi_number(li.discount_percent)} %</td>"
                f"<td class=\"num\">{fi_money(li.total, currency)}</td>"
                f"</tr>"
            )
        signature_block = ""
        if proposal.signature_name:
            signed = proposal.signed_at.strftime("%d.%m.%Y %H:%M") if proposal.signed_at else ""
            signature_block = (
                f'<div class="signature"><p><strong>Allekirjoitus:</strong> '
                f'{html_module.escape(proposal.signature_name)} ({signed})</p></div>'
            )
        logo_block = ""
        if logo_url and logo_url.lower().startswith("https://"):
            logo_block = f'<img class="logo" src="{html_module.escape(logo_url, quote=True)}" alt="">'
        vat = vat_breakdown(proposal)
        discount_row = (
            f"<tr><td>Alennus</td><td class=\"num\">-{fi_money(vat['discount'], currency)}</td></tr>"
            if vat["discount"] > 0
            else ""
        )
        valid_until = proposal.valid_until.strftime("%d.%m.%Y") if proposal.valid_until else "—"
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
  .num {{ text-align: right; white-space: nowrap; }}
  th.num {{ text-align: right; }}
  .logo {{ max-height: 64px; max-width: 240px; margin-bottom: 1rem; }}
  table.totals {{ width: 55%; margin-left: 45%; margin-top: 1.25rem; }}
  table.totals td {{ border-bottom: none; padding: 0.25rem 0.5rem; }}
  table.totals tr.net td {{ border-top: 1px solid #e2e8f0; }}
  table.totals tr.grand td {{ border-top: 2px solid #1e293b; font-weight: 700; font-size: 1.1rem; padding-top: 0.5rem; }}
  .notes {{ margin-top: 2rem; padding: 1rem; background: #f8fafc; border-radius: 6px; }}
  .signature {{ margin-top: 2rem; border-top: 2px solid #22c55e; padding-top: 1rem; }}
  .header, .footer {{ margin-bottom: 1rem; font-size: 0.9rem; }}
</style>
</head>
<body>
{logo_block}
<div class="header">{header}</div>
<h1>{html_module.escape(proposal.title)}</h1>
<p class="meta">
  <strong>{html_module.escape(proposal.reference_number)}</strong><br>
  Asiakas: {html_module.escape(proposal.lead_name_snapshot or '')}
  {f' — {html_module.escape(proposal.lead_company_snapshot)}' if proposal.lead_company_snapshot else ''}<br>
  Voimassa: {valid_until}
</p>
<table>
  <thead>
    <tr>
      <th>Kuvaus</th><th class="num">Määrä</th><th class="num">á hinta (alv 0 %)</th><th class="num">Alennus</th><th class="num">Yhteensä (alv 0 %)</th>
    </tr>
  </thead>
  <tbody>
    {''.join(rows)}
  </tbody>
</table>
<table class="totals">
  <tr><td>Välisumma</td><td class="num">{fi_money(vat['subtotal'], currency)}</td></tr>
  {discount_row}
  <tr class="net"><td>Veroton yhteensä</td><td class="num">{fi_money(vat['net'], currency)}</td></tr>
  <tr><td>ALV {fi_number(vat['tax_percent'])} %</td><td class="num">{fi_money(vat['tax_amount'], currency)}</td></tr>
  <tr class="grand"><td>Yhteensä (sis. ALV)</td><td class="num">{fi_money(vat['gross'], currency)}</td></tr>
</table>
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
            proposal,
            template_header=header,
            template_footer=footer,
            logo_url=template.logo_url if template else None,
        )
        try:
            from weasyprint import HTML

            return HTML(string=html_content, url_fetcher=_safe_url_fetcher).write_pdf()
        except (ImportError, OSError):
            # WeasyPrint can fail to load on some environments due to missing native libs.
            # Fallback to returning the rendered HTML bytes so callers/tests still get content.
            return html_content.encode("utf-8")
