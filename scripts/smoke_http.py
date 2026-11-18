"""HTTP smoke test of a deployed FlowLeads, from outside (no login):

    python scripts/smoke_http.py https://flowleads-crm-zv0a.onrender.com

Checks the health endpoints, the login page, a vendored stylesheet and that
the new pages exist (they redirect to login without a session)."""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

CHECKS = (
    ("/api/v1/health", (200,)),
    ("/api/v1/health/db", (200,)),
    ("/auth/login", (200,)),
    ("/static/css/v4.css", (200,)),
    ("/hinnasto", (200,)),
    ("/targets", (302, 401)),
    ("/admin/activity", (302, 401, 403)),
    ("/admin/announcements", (302, 401, 403)),
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main(base: str) -> int:
    opener = urllib.request.build_opener(_NoRedirect)
    failed = 0
    for path, expected in CHECKS:
        try:
            status = opener.open(base.rstrip("/") + path, timeout=30).status
        except urllib.error.HTTPError as exc:
            status = exc.code
        except Exception as exc:  # noqa: BLE001
            status = f"virhe: {exc}"
        ok = status in expected
        failed += not ok
        print(f"  [{'OK  ' if ok else 'VIRHE'}] {path}: {status}")
    print(f"\n{failed} virhettä.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"))
