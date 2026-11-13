"""A small allowlist sanitizer for HTML a customer types into settings (the
proposal header and footer). That HTML is shown on the app's own origin
(proposal preview) and the CSP allows inline scripts, so only plain
formatting survives: no scripts, styles, event handlers or javascript: URLs."""

from __future__ import annotations

import html
from html.parser import HTMLParser

ALLOWED_TAGS = {
    "p", "br", "div", "span", "strong", "b", "em", "i", "u", "small",
    "h1", "h2", "h3", "h4", "ul", "ol", "li", "a", "img", "hr",
    "table", "tr", "td", "th", "tbody", "thead",
}
VOID_TAGS = {"br", "img", "hr"}
DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math"}


def _safe_url(value: str, *, image: bool) -> str | None:
    value = (value or "").strip()
    lowered = value.lower()
    if lowered.startswith("https://") or (not image and lowered.startswith(("http://", "mailto:", "tel:"))):
        return value
    return None


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROP_CONTENT:
            self.skip += 1
            return
        if self.skip or tag not in ALLOWED_TAGS:
            return
        kept = []
        for name, value in attrs:
            if tag == "a" and name == "href":
                url = _safe_url(value, image=False)
                if url:
                    kept.append(f' href="{html.escape(url)}" rel="noopener"')
            elif tag == "img" and name == "src":
                url = _safe_url(value, image=True)
                if url:
                    kept.append(f' src="{html.escape(url)}"')
            elif tag == "img" and name == "alt":
                kept.append(f' alt="{html.escape(value or "")}"')
        self.out.append(f"<{tag}{''.join(kept)}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data))


def sanitize_html(value: str | None) -> str:
    if not value:
        return ""
    parser = _Sanitizer()
    parser.feed(value)
    parser.close()
    return "".join(parser.out)
