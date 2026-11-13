"""Build the FlowLeads listing-catalogue n8n workflows.

    python n8n/tools/build_catalogue_workflows.py

Writes into n8n/workflows/:
    flowleads-crawler.json     Oikotie -> CRM catalogue (incremental + full pass)
    flowleads-analyzer.json    one AI call per listing -> renovations by type
    flowleads-enricher.json    Finder/Asiakastieto contacts for matched listings

Oikotie request nodes, the detail-page fetch and the whole Finder/Asiakastieto
fallback chain are lifted from the production Lead Worker export
(n8n/source/lead-worker-export.json) via build_workflows.build_worker(), so they
behave exactly as in production (with that module's bug fixes applied).
Architecture: docs/ARKKITEHTUURI_KOHDEKATALOGI.md
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_workflows import (  # noqa: E402
    _id,
    build_worker,
    code,
    connect,
    node,
    secret_headers,
    workflow,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "n8n" / "source" / "lead-worker-export.json"
OUT_DIR = ROOT / "n8n" / "workflows"

CRAWLER_NAME = "FlowLeads – Kohdekeruu (Crawler)"
ANALYZER_NAME = "FlowLeads – Kohdeanalyysi (Analyzer)"
ENRICHER_NAME = "FlowLeads – Yhteystietohaku (Enricher)"


def lib_nodes() -> dict:
    return {n["name"]: n for n in build_worker(SOURCE)["nodes"]}


def take(lib, name, pos, *, rename=None, **overrides):
    n = copy.deepcopy(lib[name])
    n["id"] = _id()
    n["position"] = pos
    if rename:
        n["name"] = rename
    n.update(overrides)
    return n


def config_node(pos):
    return node(
        "SET – Config",
        "n8n-nodes-base.set",
        {
            "assignments": {
                "assignments": [
                    {"id": _id(), "name": "crm_base_url", "value": "https://flowleads-crm-zv0a.onrender.com", "type": "string"},
                    {"id": _id(), "name": "n8n_secret", "value": "VAIHDA_TAHAN_N8N_MASTER_SECRET", "type": "string"},
                ]
            },
            "options": {},
        },
        pos,
        3.4,
        notes="CRM-osoite ja sama arvo kuin Renderin N8N_MASTER_SECRET. Sama asetus on jokaisessa kolmessa workflowssa.",
    )


def schedule(name, pos, *, minutes=None, hours=None):
    rule = {"field": "minutes", "minutesInterval": minutes} if minutes else {"field": "hours", "hoursInterval": hours}
    return node(name, "n8n-nodes-base.scheduleTrigger", {"rule": {"interval": [rule]}}, pos, 1.2)


def crm_post(name, path_expr, body_expr, pos, **extra):
    return node(
        name,
        "n8n-nodes-base.httpRequest",
        {
            "method": "POST",
            "url": "={{ $json.crm_base_url }}" + path_expr,
            "sendHeaders": True,
            "headerParameters": secret_headers(),
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": body_expr,
            "options": {},
        },
        pos,
        4.2,
        retryOnFail=True,
        maxTries=3,
        waitBetweenTries=5000,
        **extra,
    )


def wait_node(name, seconds, pos):
    return node(name, "n8n-nodes-base.wait", {"amount": seconds, "unit": "seconds"}, pos, 1.1, webhookId=_id())


def bool_if(name, expr, pos):
    return node(
        name,
        "n8n-nodes-base.if",
        {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                "conditions": [
                    {
                        "id": _id(),
                        "leftValue": expr,
                        "rightValue": "",
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                    }
                ],
                "combinator": "and",
            },
            "looseTypeValidation": True,
            "options": {},
        },
        pos,
        2.2,
    )


SPLIT_WITH_CONFIG = """
const cfg = $('SET – Config').first().json;
const rows = ($input.first().json.%s || []);
return rows.map(r => ({ json: { ...r, %s crm_base_url: String(cfg.crm_base_url || '').replace(/\\/$/, ''), n8n_secret: cfg.n8n_secret } }));
"""


# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------


def build_crawler(lib):
    nodes, c = [], {}
    nodes += [
        schedule("Every hour", [0, 0], hours=1),
        node("Manual Trigger", "n8n-nodes-base.manualTrigger", {}, [0, 200], 1),
        config_node([220, 100]),
        node(
            "HTTP – Get Crawl Plan",
            "n8n-nodes-base.httpRequest",
            {
                "url": "={{ $json.crm_base_url.replace(/\\/$/, '') }}/api/v1/n8n/listings/crawl-plan",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "X-N8N-Secret", "value": "={{ $json.n8n_secret }}"}]},
                "options": {},
            },
            [440, 100],
            4.2,
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=5000,
        ),
        code("CODE – Split Tasks", SPLIT_WITH_CONFIG % ("tasks", ""), [660, 100]),
        node("LOOP – Tasks", "n8n-nodes-base.splitInBatches", {"options": {}}, [880, 100], 3,
             notes="Yksi tehtävä = yksi maakunta (incremental tai full-pass-viipale)."),
    ]

    search = take(lib, "HTTP – Fetch Oikotie Search Page", [1100, 200], onError="continueRegularOutput")
    search["parameters"]["url"] = "={{ 'https://asunnot.oikotie.fi/myytavat-asunnot/' + $json.slug }}"
    search["parameters"]["queryParameters"]["parameters"] = [
        {"name": "cardType", "value": "100"},
        {"name": "offset", "value": "0"},
        {"name": "limit", "value": "24"},
    ]
    nodes.append(search)

    nodes.append(
        code(
            "PARSE – Extract apiLocation",
            """
const task = $('LOOP – Tasks').first().json;
const html = String($input.first()?.json?.data || $input.first()?.json?.body || '');
let parsed = null;
const m = html.match(/"locations"\\s*:\\s*(\\[[^\\]]*\\])/i);
if (m && m[1]) { try { parsed = JSON.parse(m[1]); } catch (e) { parsed = null; } }
const locations = Array.isArray(parsed) && parsed.length
  ? parsed.map(loc => [loc.id, loc.type, loc.name])
  : [task.slug];
return [{ json: { ...task, locations } }];
""",
            [1320, 200],
        )
    )
    boot = take(lib, "HTTP – Fetch Oikotie API Bootstrap", [1540, 200], onError="continueRegularOutput")
    # The production Lead Worker never sent a cookie here: its expression read
    # headers from an item that has none, which n8n resolves to "no header".
    # Sending an empty `cookie:` header instead is a wire-level difference no
    # browser makes, so the header is left out to match production exactly.
    boot_headers = boot["parameters"]["headerParameters"]["parameters"]
    boot["parameters"]["headerParameters"]["parameters"] = [h for h in boot_headers if h["name"].lower() != "cookie"]
    nodes.append(boot)
    nodes.append(
        code(
            "TRANSFORM – Extract Session Cookie",
            """
const task = $('PARSE – Extract apiLocation').first().json;
const r = $input.first()?.json || {};
const html = String(r.data || r.body || '');
const meta = (name) => {
  const m = html.match(new RegExp(`<meta[^>]+name=["']${name}["'][^>]+content=["']([^"']+)["']`, 'i'));
  return m ? m[1] : '';
};
const setCookie = r.headers?.['set-cookie'] || [];
const cookie = Array.isArray(setCookie) ? setCookie.map(x => String(x).split(';')[0]).join('; ') : '';
return [{ json: {
  ...task,
  cookie,
  otaToken: meta('api-token'),
  otaCuid: meta('cuid'),
  otaLoaded: meta('loaded'),
  page_in_run: 1,
} }];
""",
            [1760, 200],
        )
    )
    nodes.append(
        code(
            "CODE – Page Context",
            """
// Every page passes through here right before the API call, so later nodes
// read the current page's context with $('CODE – Page Context').first().
return [{ json: $input.first().json }];
""",
            [1980, 200],
        )
    )
    api = take(lib, "HTTP – Fetch Oikotie Listings API", [2200, 200], onError="continueRegularOutput",
               retryOnFail=True, maxTries=2, waitBetweenTries=5000)
    api["parameters"]["queryParameters"]["parameters"] = [
        {"name": "cardType", "value": "100"},
        {"name": "limit", "value": "24"},
        {"name": "offset", "value": "={{ $json.offset }}"},
        {"name": "sortBy", "value": "published_sort_desc"},
        {"name": "locations", "value": "={{ JSON.stringify($json.locations) }}"},
    ]
    nodes.append(api)
    nodes.append(
        code(
            "CODE – Normalize Cards",
            """
const ctx = $('CODE – Page Context').first().json;
const r = $input.first()?.json || {};
let error = '';
if (r.error) error = typeof r.error === 'string' ? r.error : (r.error.message || JSON.stringify(r.error));
const cards = r.cards || r.data?.cards;
if (!error && !Array.isArray(cards)) error = 'Unexpected Oikotie response: ' + JSON.stringify(r).slice(0, 200);
const listings = (Array.isArray(cards) ? cards : []).map(card => {
  const loc = card.location || {};
  const d = card.data || {};
  const b = card.buildingData || card.building || {};
  return {
    external_id: String(card.cardId || card.id || ''),
    url: card.url || '',
    kaupunki: loc.city || loc.municipality || '',
    osoite: loc.address || '',
    hinta: d.price || '',
    alue: d.size || d.sizeMax || '',
    description: d.description || '',
    published_at: card.published || d.published || card.meta?.published || null,
    // optional: the CRM falls back to the AI analysis when these are missing
    build_year: b.year || d.buildYear || d.year || null,
    building_type: b.buildingType || d.buildingType || card.buildingType || null,
  };
});
const body = {
  maakunta: ctx.maakunta,
  mode: ctx.mode,
  offset: Number(ctx.offset || 0),
  page_in_run: Number(ctx.page_in_run || 1),
  listings,
};
if (error) body.error = error.slice(0, 1000);
return [{ json: { ctx, crm_base_url: ctx.crm_base_url, n8n_secret: ctx.n8n_secret, body } }];
""",
            [2420, 200],
        )
    )
    nodes.append(
        crm_post(
            "HTTP – CRM Ingest",
            "/api/v1/n8n/listings/ingest",
            "={{ JSON.stringify($json.body) }}",
            [2640, 200],
            onError="continueRegularOutput",
        )
    )
    nodes.append(
        code(
            "CODE – Next Page",
            """
const { ctx } = $('CODE – Normalize Cards').first().json;
const r = $input.first()?.json || {};
const page = Number(ctx.page_in_run || 1);
// The CRM decides: incremental stops once a page brings nothing new, the
// full pass stops after its per-run slice; max_pages is a local safety cap.
// Oikotie's bot protection answers 403 (captcha) or 429 when we go too fast.
// Then the whole run stops: no more pages and no further regions this hour.
const err = String($('CODE – Normalize Cards').first().json.body.error || '');
const blocked = /(?<!\\d)(403|429)(?!\\d)|captcha/i.test(err);
const more = !blocked && r.continue === true && page < Number(ctx.max_pages || 1);
return [{ json: { ...ctx, more, blocked, offset: Number(r.next_offset ?? (Number(ctx.offset || 0) + 24)), page_in_run: page + 1 } }];
""",
            [2860, 200],
        )
    )
    # true output is deliberately unconnected: a blocked run ends here.
    nodes.append(bool_if("IF – Oikotie Blocked", "={{ $json.blocked }}", [3080, 200]))
    nodes.append(bool_if("IF – More Pages", "={{ $json.more }}", [3300, 200]))
    nodes.append(wait_node("WAIT – Between Pages", 10, [3520, 340]))

    connect(c, "Every hour", "SET – Config")
    connect(c, "Manual Trigger", "SET – Config")
    connect(c, "SET – Config", "HTTP – Get Crawl Plan")
    connect(c, "HTTP – Get Crawl Plan", "CODE – Split Tasks")
    connect(c, "CODE – Split Tasks", "LOOP – Tasks")
    connect(c, "LOOP – Tasks", "HTTP – Fetch Oikotie Search Page", out_index=1)
    connect(c, "HTTP – Fetch Oikotie Search Page", "PARSE – Extract apiLocation")
    connect(c, "PARSE – Extract apiLocation", "HTTP – Fetch Oikotie API Bootstrap")
    connect(c, "HTTP – Fetch Oikotie API Bootstrap", "TRANSFORM – Extract Session Cookie")
    connect(c, "TRANSFORM – Extract Session Cookie", "CODE – Page Context")
    connect(c, "CODE – Page Context", "HTTP – Fetch Oikotie Listings API")
    connect(c, "HTTP – Fetch Oikotie Listings API", "CODE – Normalize Cards")
    connect(c, "CODE – Normalize Cards", "HTTP – CRM Ingest")
    connect(c, "HTTP – CRM Ingest", "CODE – Next Page")
    connect(c, "CODE – Next Page", "IF – Oikotie Blocked")
    connect(c, "IF – Oikotie Blocked", "IF – More Pages", out_index=1)
    connect(c, "IF – More Pages", "WAIT – Between Pages", out_index=0)
    connect(c, "IF – More Pages", "LOOP – Tasks", out_index=1)
    connect(c, "WAIT – Between Pages", "CODE – Page Context")
    return workflow(CRAWLER_NAME, nodes, c,
                    description="Hourly: crawl Oikotie (newest first + a slice of the weekly full pass) into the FlowLeads listing catalogue.")


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """Olet kiinteistöalan analyytikko. Saat suomalaisen myytävän asunnon ilmoituksen tekstin.
Tehtäväsi on poimia taloyhtiön tiedot ja kohteen TULEVAT remontit.

Remonttityypit (käytä TÄSMÄLLEEN näitä nimiä):
__TYPES__

Säännöt tuleville remonteille:
- Ota mukaan vain tuleva, suunniteltu, päätetty, budjetoitu, ehdotettu, kuntoarviossa tai PTS:ssä mainittu tai muuten vielä tekemätön työ.
- ÄLÄ ota mukaan jo tehtyjä, valmistuneita tai aiemmin toteutettuja remontteja.
- Luokittele jokainen tuleva remontti yhteen tyyppiin. Jos sopivaa tyyppiä ei ole, käytä "Muu remontti".
- "Lukkoremontti / kulunvalvonta": lukitus, lukkojen tai avainten uusiminen, sarjoitus, sähkölukitus, iLOQ, kulunvalvonta tai kulkuoikeudet oviin ja sisäänkäynteihin. EI kameravalvontaa, murtohälytintä, paloilmoitinta eikä pelkkää ovipuhelinta.
- "Putkiremontti": linjasaneeraus, putkiremontti, viemärien, käyttövesi- tai vesijohtoputkien uusiminen tai sukitus.
- "Sähkö- ja tietoliikennejärjestelmät": sähköverkon, sähköpääkeskuksen, nousujohtojen, antenni-, valokuitu- tai tietoliikenneverkon uusiminen.
- "Lämmitysjärjestelmän remontti": lämmönjakohuone, maalämpö, kaukolämmön laitteet, patteriverkoston perussäätö tai uusiminen.
- "Julkisivuremontti": julkisivu, ikkunat, parvekkeet.
- "Katto- ja vesikattoremontti": katto, vesikatto, yläpohja.
- "Huoneistoremontit": huoneistojen kylpyhuoneet, keittiöt tai muut huoneistokohtaiset remontit.
- confidence 0–100 = kuinka varmasti teksti kertoo juuri tämän tyyppisestä TULEVASTA työstä.
- evidence = lyhyt suora lainaus tekstistä. timing = esim. "2027", "suunnitteilla", "PTS 2026–2028" tai "".
- Älä keksi mitään, mitä tekstissä ei lue. Jos tulevia remontteja ei ole, palauta tyhjä lista.

Yhteystiedot: poimi vain tekstissä näkyvät tiedot: taloyhtiön nimi, isännöitsijä (isännöintiyritys tai, jos yritystä ei mainita, henkilö), isännöitsijän henkilön nimi (isannoitsija_henkilo, jos teksti nimeää sekä yrityksen että henkilön), isännöitsijän puhelin ja sähköposti sekä huoltoyhtiö (kiinteistöhuolto) ja sen puhelin. Muuten "".

Rakennus: rakennusvuosi = rakennuksen valmistumis- tai rakennusvuosi numerona (esim. 1972), tai null jos tekstissä ei ole. talotyyppi = yksi näistä: "Kerrostalo", "Rivitalo", "Paritalo", "Omakotitalo", "Muu", tai "" jos tekstistä ei selviä (luhtitalo = "Kerrostalo", erillistalo = "Omakotitalo").

Palauta VAIN validi JSON ilman markdownia:
{"taloyhtio":"","isannoitsija":"","isannoitsija_henkilo":"","isannoitsija_puh":"","isannoitsija_email":"","huoltoyhtio":"","huoltoyhtio_puh":"","rakennusvuosi":null,"talotyyppi":"","tulevat_remontit":"lyhyt tiivistelmä tulevista remonteista","renovations":[{"remonttityyppi":"","confidence":0,"timing":"","evidence":""}]}"""


def build_analyzer(lib):
    nodes, c = [], {}
    nodes += [
        schedule("Every 10 minutes", [0, 0], minutes=10),
        node("Manual Trigger", "n8n-nodes-base.manualTrigger", {}, [0, 200], 1),
        config_node([220, 100]),
        code(
            "CODE – Start",
            """
const cfg = $input.first().json;
return [{ json: { started_at: Date.now(), crm_base_url: String(cfg.crm_base_url || '').replace(/\\/$/, ''), n8n_secret: cfg.n8n_secret } }];
""",
            [440, 100],
        ),
        node(
            "HTTP – Claim Listings",
            "n8n-nodes-base.httpRequest",
            {
                "method": "POST",
                "url": "={{ $('CODE – Start').first().json.crm_base_url }}/api/v1/n8n/listings/analysis/claim",
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [
                        {"name": "X-N8N-Secret", "value": "={{ $('CODE – Start').first().json.n8n_secret }}"},
                        {"name": "Content-Type", "value": "application/json"},
                    ]
                },
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": '{"limit": 20}',
                "options": {},
            },
            [660, 100],
            4.2,
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=5000,
        ),
        code(
            "CODE – Split Listings",
            """
const start = $('CODE – Start').first().json;
const r = $input.first().json || {};
const types = r.remonttityypit || [];
return (r.listings || []).map(l => ({ json: { ...l, remonttityypit: types, crm_base_url: start.crm_base_url, n8n_secret: start.n8n_secret } }));
""",
            [880, 100],
        ),
        node(
            "LOOP – Listings",
            "n8n-nodes-base.splitInBatches",
            {"options": {"reset": "={{ $prevNode.name === 'CODE – Split Listings' }}"}},
            [1100, 100],
            3,
        ),
        wait_node("WAIT – Rate Limit", 1, [1320, 260]),
        # Imported listings (fetch_detail === false) carry their full text in
        # `description`; only Oikotie listings have a detail page to fetch.
        bool_if("IF – Fetch Detail Page", "={{ $json.fetch_detail !== false }}", [1430, 260]),
    ]
    detail = take(lib, "HTTP – Fetch Listing Detail Page", [1540, 260], onError="continueRegularOutput")
    detail["parameters"]["url"] = "={{ $('LOOP – Listings').first().json.url }}"
    for h in detail["parameters"]["headerParameters"]["parameters"]:
        if h["name"] == "Referer":
            h["value"] = "https://asunnot.oikotie.fi/myytavat-asunnot"
    nodes.append(detail)

    nodes.append(
        code(
            "CODE – Build Analysis Prompt",
            """
const listing = $('LOOP – Listings').first().json;
const useOwnText = listing.fetch_detail === false;
const h = useOwnText ? {} : ($input.first()?.json || {});
const status = Number(h.statusCode || 0);
const html = String(h.pageHtml ?? h.body ?? h.data ?? '').slice(0, 200000);

let text = html
  .replace(/<script[\\s\\S]*?<\\/script>/gi, ' ')
  .replace(/<style[\\s\\S]*?<\\/style>/gi, ' ')
  .replace(/<noscript[\\s\\S]*?<\\/noscript>/gi, ' ')
  .replace(/<svg[\\s\\S]*?<\\/svg>/gi, ' ')
  .replace(/<img[^>]*>/gi, ' ')
  .replace(/<[^>]+>/g, ' ')
  .replace(/&nbsp;/gi, ' ').replace(/&amp;/gi, '&').replace(/&quot;/gi, '"').replace(/&#39;/gi, "'")
  .replace(/\\s+/g, ' ')
  .trim();
for (const marker of ['Taloyhtiö', 'Tulevat remontit', 'Asunnon lisätiedot', 'Perustiedot', 'Kohdenumero', 'Rakennuksen tiedot', 'Asunnon kuvaus']) {
  const idx = text.indexOf(marker);
  if (idx >= 0) { text = text.slice(idx); break; }
}
text = text.slice(0, 12000);
if (useOwnText) text = String(listing.description || '').replace(/\\s+/g, ' ').trim().slice(0, 12000);

const ok = useOwnText
  ? text.length >= 30
  : !h.error && (status === 0 || (status >= 200 && status < 300)) && text.length > 200;
const PROMPT = %s;
const types = (listing.remonttityypit || []).map(t => '- ' + t).join('\\n');
return [{ json: {
  ...listing,
  ok,
  error: ok ? '' : (useOwnText ? 'Imported listing has no text to analyse' : `Detail page failed (status ${status || 'n/a'}, ${text.length} chars)`),
  ai_system_prompt: PROMPT.replace('__TYPES__', types),
  ai_user_prompt: useOwnText
    ? `Taloyhtiö: ${listing.taloyhtio || ''}\\nKaupunki: ${listing.kaupunki || ''}\\nOsoite: ${listing.osoite || ''}\\n\\nKohteen teksti:\\n${text}`
    : `URL: ${listing.url}\\nKaupunki: ${listing.kaupunki || ''}\\nOsoite: ${listing.osoite || ''}\\nIlmoituksen kuvaus: ${listing.description || ''}\\n\\nKohdesivun teksti:\\n${text}`,
} }];
"""
            % json.dumps(ANALYSIS_PROMPT, ensure_ascii=False),
            [1760, 260],
        )
    )
    nodes.append(bool_if("IF – Page OK", "={{ $json.ok }}", [1980, 260]))

    ai = take(lib, "AI – Extract Company and Contact Clues", [2200, 180], rename="AI – Analyze Listing")
    ai["parameters"]["responses"]["values"] = [
        {"role": "system", "content": "={{ $json.ai_system_prompt }}"},
        {"content": "={{ $json.ai_user_prompt }}"},
    ]
    nodes.append(ai)

    nodes.append(
        code(
            "CODE – Parse Analysis",
            """
const p = $('CODE – Build Analysis Prompt').first().json;
const base = { listing_id: p.listing_id, crm_base_url: p.crm_base_url, n8n_secret: p.n8n_secret };
if (!p.ok) return [{ json: { ...base, body: { status: 'failed', error: p.error } } }];

const out = $input.first()?.json || {};
if (out.error) {
  const e = typeof out.error === 'string' ? out.error : (out.error.message || JSON.stringify(out.error));
  return [{ json: { ...base, body: { status: 'failed', error: ('AI: ' + e).slice(0, 1000) } } }];
}
const raw = String(out.output?.[0]?.content?.[0]?.text || out.output?.[0]?.text || out.text || '')
  .trim().replace(/^```json\\s*/i, '').replace(/^```\\s*/i, '').replace(/```$/i, '').trim();
let a;
try { a = JSON.parse(raw); } catch (e) {
  return [{ json: { ...base, body: { status: 'failed', error: 'AI JSON parse failed: ' + raw.slice(0, 300) } } }];
}
const s = v => (typeof v === 'string' ? v.trim() : '');
const renovations = (Array.isArray(a.renovations) ? a.renovations : [])
  .filter(r => r && typeof r === 'object')
  .map(r => ({
    remonttityyppi: s(r.remonttityyppi),
    confidence: Number(r.confidence || 0),
    timing: s(r.timing),
    evidence: s(r.evidence),
  }));
return [{ json: { ...base, body: {
  status: 'done',
  taloyhtio: s(a.taloyhtio),
  isannoitsija: s(a.isannoitsija),
  isannoitsija_henkilo: s(a.isannoitsija_henkilo),
  isannoitsija_puh: s(a.isannoitsija_puh),
  isannoitsija_email: s(a.isannoitsija_email),
  huoltoyhtio: s(a.huoltoyhtio),
  huoltoyhtio_puh: s(a.huoltoyhtio_puh),
  tulevat_remontit: s(a.tulevat_remontit),
  rakennusvuosi: Number.isFinite(Number(a.rakennusvuosi)) && Number(a.rakennusvuosi) > 0 ? Number(a.rakennusvuosi) : null,
  talotyyppi: s(a.talotyyppi),
  renovations,
} } }];
""",
            [2420, 260],
        )
    )
    nodes.append(
        crm_post(
            "HTTP – CRM Save Analysis",
            "/api/v1/n8n/listings/{{ $json.listing_id }}/analysis",
            "={{ JSON.stringify($json.body) }}",
            [2640, 260],
            onError="continueRegularOutput",
        )
    )
    nodes.append(
        code(
            "CODE – More To Claim",
            """
const start = $('CODE – Start').first().json;
const claimed = ($('HTTP – Claim Listings').first().json.listings || []).length;
const elapsed = Date.now() - Number(start.started_at || Date.now());
// Keep claiming while the queue is full and we are well inside the run budget
// (next scheduled run starts in 10 min).
return [{ json: { more: claimed >= 20 && elapsed < 7 * 60 * 1000 } }];
""",
            [1320, -80],
        )
    )
    nodes.append(bool_if("IF – Claim Again", "={{ $json.more }}", [1540, -80]))

    connect(c, "Every 10 minutes", "SET – Config")
    connect(c, "Manual Trigger", "SET – Config")
    connect(c, "SET – Config", "CODE – Start")
    connect(c, "CODE – Start", "HTTP – Claim Listings")
    connect(c, "HTTP – Claim Listings", "CODE – Split Listings")
    connect(c, "CODE – Split Listings", "LOOP – Listings")
    connect(c, "LOOP – Listings", "CODE – More To Claim", out_index=0)
    connect(c, "LOOP – Listings", "WAIT – Rate Limit", out_index=1)
    connect(c, "WAIT – Rate Limit", "IF – Fetch Detail Page")
    connect(c, "IF – Fetch Detail Page", "HTTP – Fetch Listing Detail Page", out_index=0)
    connect(c, "IF – Fetch Detail Page", "CODE – Build Analysis Prompt", out_index=1)
    connect(c, "HTTP – Fetch Listing Detail Page", "CODE – Build Analysis Prompt")
    connect(c, "CODE – Build Analysis Prompt", "IF – Page OK")
    connect(c, "IF – Page OK", "AI – Analyze Listing", out_index=0)
    connect(c, "IF – Page OK", "CODE – Parse Analysis", out_index=1)
    connect(c, "AI – Analyze Listing", "CODE – Parse Analysis")
    connect(c, "CODE – Parse Analysis", "HTTP – CRM Save Analysis")
    connect(c, "HTTP – CRM Save Analysis", "LOOP – Listings")
    connect(c, "CODE – More To Claim", "IF – Claim Again")
    connect(c, "IF – Claim Again", "HTTP – Claim Listings", out_index=0)
    return workflow(ANALYZER_NAME, nodes, c,
                    description="Every 10 min: claim unanalysed listings, one AI call each -> renovations by type -> CRM matches them to profiles.")


# ---------------------------------------------------------------------------
# Contact Enricher
# ---------------------------------------------------------------------------

FALLBACK_CHAIN = [
    "TRANSFORM – Build Company Search Query",
    "TRANSFORM – Build URLs",
    "IF – Finder Search Enabled",
    "HTTP – Search Finder.fi",
    "PARSE – Extract Finder Company URL",
    "IF – Finder Company URL Found",
    "HTTP – Fetch Finder Company Page",
    "STORE – Save Finder Response",
    "HTTP – Search Asiakastieto",
    "PARSE – Extract Asiakastieto Company URL",
    "HTTP – Fetch Asiakastieto Company Page",
    "PARSE – Combine Company Search Responses",
    "TRANSFORM – Normalize Fallback Candidates",
    "AI – Extract Fallback Contact Info",
    "PARSE – Normalize Fallback Contact Data",
]


def build_enricher(lib):
    nodes, c = [], {}
    nodes += [
        schedule("Every 30 minutes", [0, 0], minutes=30),
        node("Manual Trigger", "n8n-nodes-base.manualTrigger", {}, [0, 200], 1),
        config_node([220, 100]),
        crm_post(
            "HTTP – Claim Contacts",
            "/api/v1/n8n/listings/contacts/claim",
            '{"limit": 10}',
            [440, 100],
        ),
        code("CODE – Split Listings", SPLIT_WITH_CONFIG % ("listings", ""), [660, 100]),
        node("LOOP – Listings", "n8n-nodes-base.splitInBatches", {"options": {}}, [880, 100], 3),
        code(
            "CODE – Map Listing",
            """
// Field names expected by the production fallback chain.
const l = $json;
return { json: {
  ...l,
  ID: String(l.listing_id),
  lead_key: String(l.listing_id),
  'Isännöitsijä': l.isannoitsija || '',
  'Taloyhtiö': l.taloyhtio || '',
  'Osoite': l.osoite || '',
  'Kaupunki': l.kaupunki || '',
  'IN puh': '',
  'IN email': '',
} };
""",
            [1100, 200],
            each=True,
        ),
    ]
    x = 1320
    for name in FALLBACK_CHAIN:
        nodes.append(take(lib, name, [x, 200]))
        x += 220
    nodes.append(
        code(
            "CODE – Build Contacts Body",
            """
const listing = $('LOOP – Listings').first().json;
const j = $input.first()?.json || {};
const s = v => (v === undefined || v === null) ? '' : String(v).trim();
const phone = s(j.fallback_in_puh) || s(j.fallbackPrimaryPhone);
const email = s(j.fallback_in_email);
const website = s(j.fallbackPrimaryWebsite);
const name = s(j.fallback_isannointiyhtio) || s(j.fallback_isannoitsija);
const found = !!(phone || email);
const body = found
  ? { status: 'done', isannoitsija: name, phone, email, website, source: s(j.fallback_source) || s(j.fallbackSourceHint) || 'fallback' }
  : { status: 'not_found' };
return [{ json: { listing_id: listing.listing_id, crm_base_url: listing.crm_base_url, n8n_secret: listing.n8n_secret, body } }];
""",
            [x, 200],
        )
    )
    nodes.append(
        crm_post(
            "HTTP – CRM Save Contacts",
            "/api/v1/n8n/listings/{{ $json.listing_id }}/contacts",
            "={{ JSON.stringify($json.body) }}",
            [x + 220, 200],
            onError="continueRegularOutput",
        )
    )

    connect(c, "Every 30 minutes", "SET – Config")
    connect(c, "Manual Trigger", "SET – Config")
    connect(c, "SET – Config", "HTTP – Claim Contacts")
    connect(c, "HTTP – Claim Contacts", "CODE – Split Listings")
    connect(c, "CODE – Split Listings", "LOOP – Listings")
    connect(c, "LOOP – Listings", "CODE – Map Listing", out_index=1)
    connect(c, "CODE – Map Listing", "TRANSFORM – Build Company Search Query")
    chain = FALLBACK_CHAIN
    straight = [
        ("TRANSFORM – Build Company Search Query", "TRANSFORM – Build URLs", 0),
        ("TRANSFORM – Build URLs", "IF – Finder Search Enabled", 0),
        ("IF – Finder Search Enabled", "HTTP – Search Finder.fi", 0),
        ("IF – Finder Search Enabled", "CODE – Build Contacts Body", 1),
        ("HTTP – Search Finder.fi", "PARSE – Extract Finder Company URL", 0),
        ("PARSE – Extract Finder Company URL", "IF – Finder Company URL Found", 0),
        ("IF – Finder Company URL Found", "HTTP – Fetch Finder Company Page", 0),
        ("IF – Finder Company URL Found", "CODE – Build Contacts Body", 1),
        ("HTTP – Fetch Finder Company Page", "STORE – Save Finder Response", 0),
        ("STORE – Save Finder Response", "HTTP – Search Asiakastieto", 0),
        ("HTTP – Search Asiakastieto", "PARSE – Extract Asiakastieto Company URL", 0),
        ("PARSE – Extract Asiakastieto Company URL", "HTTP – Fetch Asiakastieto Company Page", 0),
        ("HTTP – Fetch Asiakastieto Company Page", "PARSE – Combine Company Search Responses", 0),
        ("PARSE – Combine Company Search Responses", "TRANSFORM – Normalize Fallback Candidates", 0),
        ("TRANSFORM – Normalize Fallback Candidates", "AI – Extract Fallback Contact Info", 0),
        ("AI – Extract Fallback Contact Info", "PARSE – Normalize Fallback Contact Data", 0),
        ("PARSE – Normalize Fallback Contact Data", "CODE – Build Contacts Body", 0),
    ]
    assert {a for a, _, _ in straight} | {b for _, b, _ in straight} >= set(chain)
    for a, b, i in straight:
        connect(c, a, b, out_index=i)
    connect(c, "CODE – Build Contacts Body", "HTTP – CRM Save Contacts")
    connect(c, "HTTP – CRM Save Contacts", "LOOP – Listings")
    return workflow(ENRICHER_NAME, nodes, c,
                    description="Every 30 min: Finder/Asiakastieto isännöitsijä contacts for matched listings that have none; CRM updates the leads.")


def _check(wf):
    names = [n["name"] for n in wf["nodes"]]
    assert len(names) == len(set(names)), "duplicate node names"
    for src, spec in wf["connections"].items():
        assert src in names, src
        for outs in spec["main"]:
            for o in outs:
                assert o["node"] in names, (src, o["node"])


def main():
    lib = lib_nodes()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "flowleads-crawler.json": build_crawler(lib),
        "flowleads-analyzer.json": build_analyzer(lib),
        "flowleads-enricher.json": build_enricher(lib),
    }
    for filename, wf in outputs.items():
        _check(wf)
        (OUT_DIR / filename).write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {filename}: {len(wf['nodes'])} nodes")


if __name__ == "__main__":
    main()
