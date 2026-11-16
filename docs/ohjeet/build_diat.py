"""Kokoaa 12 dian käyttöohjeen: python3 docs/ohjeet/build_diat.py (vaatii Noden ja Playwrightin)."""
import os
import re
import subprocess

DIR = os.path.dirname(os.path.abspath(__file__))
head = open(os.path.join(DIR, "_head.html"), encoding="utf-8").read()
body = open(os.path.join(DIR, "_diat_body.html"), encoding="utf-8").read()
html = head + "\n</head>\n<body>\n" + body if "</head>" not in head else head + body
total = body.count('<span class="mono">N</span>')
counter = iter(range(1, total + 1))
html = re.sub(r'<span class="mono">N</span>', lambda m: f'<span class="mono">{next(counter)} / {total}</span>', html)
out_html = os.path.join(DIR, "diat.html")
open(out_html, "w", encoding="utf-8").write(html)
render = os.path.join(DIR, "..", "esitys", "render.js")
subprocess.run(["node", render, out_html, os.path.join(DIR, "FlowLeads-kayttoohje-diat.pdf")], check=True)
print(total, "diaa")
