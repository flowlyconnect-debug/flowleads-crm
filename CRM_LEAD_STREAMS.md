# FlowLeads CRM — Liidiasetukset (Lead Routing)

**Vaihe:** V2 lisäys  
**Tavoite:** Organisaatiokohtaiset reititysasetukset automaattisesti saapuville liideille

---

## Konsepti

Jokaisella organisaatiolla on **yksi reitityssääntö**. Kaikki saapuvat liidit kulkevat automaattisesti tämän säännön läpi — oikea pipeline-vaihe, omistaja, toimiala, maakunta ja tagit asetetaan ilman manuaalista työtä.

Asiakas ei tiedä n8n:stä mitään. Kun uusi asiakas tulee, Matias luo organisaation → asetukset luodaan automaattisesti — ei manuaalista säätöä.

**Kaksi sivua (Liidivirrat + Liidiasetukset) yhdistetään yhdeksi sivuksi.**  
**API-avain näytetään vain admin- ja superadmin-rooleille.**

---

## Cursor-prompt

```
Implement lead routing settings for FlowLeads CRM. Each organization has one routing rule. When a lead arrives via POST /api/v1/leads, the CRM automatically applies the org's routing settings (pipeline stage, owner, tags).

---

## 1. DATABASE MODEL — OrgLeadSettings

Create app/streams/models.py:

class OrgLeadSettings(db.Model):
    __tablename__ = 'org_lead_settings'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False, unique=True)

    # Routing targets
    default_pipeline_stage_id = db.Column(db.Integer, db.ForeignKey('pipeline_stages.id'), nullable=True)
    default_owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    default_tags = db.Column(db.JSON, default=list)  # e.g. ["b2b", "saas"]

    # Default lead metadata (applied to every incoming lead)
    default_industry = db.Column(db.String(100), nullable=True)   # toimiala, e.g. "SaaS", "Rakentaminen"
    default_region = db.Column(db.String(100), nullable=True)     # maakunta/alue, e.g. "Uusimaa", "Pirkanmaa"

    # Stats
    last_lead_at = db.Column(db.DateTime, nullable=True)
    total_lead_count = db.Column(db.Integer, default=0, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    organization = db.relationship('Organization', backref=db.backref('lead_settings', uselist=False))
    default_pipeline_stage = db.relationship('PipelineStage')
    default_owner = db.relationship('User')

Index: unique on organization_id (one settings row per org)

Create Alembic migration for this table.
Auto-create an empty OrgLeadSettings row when a new Organization is created (add to Organization creation logic).

---

## 2. LEAD ROUTING SERVICE

Create app/streams/services.py:

class LeadRoutingService:

    @staticmethod
    def get_settings(organization_id: int) -> OrgLeadSettings:
        """
        Returns the org's routing settings.
        Creates a default row if none exists (safe fallback).
        """
        settings = OrgLeadSettings.query.filter_by(
            organization_id=organization_id
        ).first()

        if not settings:
            settings = OrgLeadSettings(organization_id=organization_id)
            db.session.add(settings)
            db.session.flush()

        return settings

    @staticmethod
    def apply_to_lead(lead, settings: OrgLeadSettings) -> None:
        """
        Apply routing settings to a lead.
        Called after lead is created/upserted, before db.session.commit().
        Only sets values if not already set on the lead (upsert: don't overwrite existing values).
        """
        # Only set stage if lead doesn't already have one
        if not lead.pipeline_stage_id and settings.default_pipeline_stage_id:
            lead.pipeline_stage_id = settings.default_pipeline_stage_id

        # Only set owner if lead doesn't already have one
        if not lead.owner_id and settings.default_owner_id:
            lead.owner_id = settings.default_owner_id

        # Set industry if not already set on lead
        if not lead.industry and settings.default_industry:
            lead.industry = settings.default_industry

        # Set region if not already set on lead
        if not lead.region and settings.default_region:
            lead.region = settings.default_region

        # Always merge tags (never replace)
        if settings.default_tags:
            existing = lead.tags or []
            lead.tags = list(set(existing + settings.default_tags))

        # Update stats
        settings.last_lead_at = datetime.utcnow()
        settings.total_lead_count = (settings.total_lead_count or 0) + 1

    @staticmethod
    def get_fallback_stage(organization_id: int):
        """
        Returns the first pipeline stage for the org as absolute fallback.
        Used when no default stage is configured in settings.
        """
        return PipelineStage.query.filter_by(
            organization_id=organization_id
        ).order_by(PipelineStage.order_index.asc()).first()

---

## 3. INTEGRATE INTO LEAD CREATION

In app/api/routes.py, inside the POST /api/v1/leads endpoint,
after the lead object is created or updated (upsert logic), before db.session.commit():

from app.streams.services import LeadRoutingService

settings = LeadRoutingService.get_settings(current_org.id)
LeadRoutingService.apply_to_lead(lead, settings)

# Final fallback: if still no stage, use first stage
if not lead.pipeline_stage_id:
    fallback = LeadRoutingService.get_fallback_stage(current_org.id)
    if fallback:
        lead.pipeline_stage_id = fallback.id

audit_log(
    action='lead_routing_applied',
    target_type='lead',
    target_id=lead.id,
    metadata={
        'stage_id': lead.pipeline_stage_id,
        'owner_id': lead.owner_id,
        'tags': lead.tags
    }
)

---

## 4. SCORE-BASED TAGGING

In the AI enrichment background task, after lead.score is set:

def apply_score_tags(lead):
    """Auto-tag based on AI score after enrichment."""
    if lead.score is None:
        return

    tags = lead.tags or []

    if lead.score >= 80 and 'hot' not in tags:
        tags.append('hot')
        db.session.add(Activity(
            organization_id=lead.organization_id,
            lead_id=lead.id,
            type='ai_score',
            content=f'AI score {lead.score}/100 — merkitty kuumaksi liidiksi'
        ))
    elif lead.score >= 60 and 'warm' not in tags:
        tags.append('warm')
    elif lead.score < 30 and 'cold' not in tags:
        tags.append('cold')

    lead.tags = tags

---

## 5. HEALTH MONITORING

In app/streams/services.py, add:

class LeadHealthService:

    STALE_DAYS = 3

    @staticmethod
    def check_all_orgs():
        """
        APScheduler daily job at 08:00 UTC.
        If an org's lead settings have last_lead_at older than STALE_DAYS
        and total_lead_count > 0 (has received leads before),
        send alert email to org admins.
        """
        cutoff = datetime.utcnow() - timedelta(days=LeadHealthService.STALE_DAYS)
        stale_orgs = OrgLeadSettings.query.filter(
            OrgLeadSettings.last_lead_at != None,
            OrgLeadSettings.last_lead_at < cutoff,
            OrgLeadSettings.total_lead_count > 0
        ).all()

        for org_settings in stale_orgs:
            org = org_settings.organization
            admins = User.query.filter_by(
                organization_id=org.id,
                role='admin',
                is_active=True
            ).all()
            for admin in admins:
                EmailService.send_lead_stale_alert(
                    to_email=admin.email,
                    org_name=org.name,
                    last_lead_at=org_settings.last_lead_at,
                    days=LeadHealthService.STALE_DAYS
                )

Register in APScheduler:
scheduler.add_job(LeadHealthService.check_all_orgs, 'cron', hour=8, minute=0, id='lead_health_check')

---

## 6. SETTINGS UI — Liidiasetukset (YKSI SIVU, KAKSI POISTETAAN)

IMPORTANT: Remove both old pages (/settings/streams and /settings/leads if they exist separately).
Replace with a single combined page.

Create template: app/templates/settings/lead_settings.html
Route: GET + PUT /settings/leads
Requires: @require_role('admin') or @require_role('superadmin')

Remove from sidebar: any "Liidivirrat" link that points to the old streams list page.
Keep only one link: "Liidiasetukset" → /settings/leads

---

### PAGE LAYOUT

Page title: "Liidiasetukset"
Subtitle: "Hallinnoi miten automaattisesti saapuvat liidit reititetään"

---

#### Section 1 — Stats (top, read-only, 3 cards in a row)

- "Liidit yhteensä" — org_settings.total_lead_count
- "Viimeksi saapunut" — relative time from last_lead_at ("2h sitten" / "Ei vielä")
- "Status":
  - gray pill "Odottaa ensimmäistä liidiä" if total_lead_count == 0
  - green pill "Aktiivinen" if last_lead_at within 3 days
  - amber pill "Ei liidejä 3+ päivään" if stale

---

#### Section 2 — Reitityssäännöt (form)

Title: "Reitityssäännöt"
Subtitle: "Nämä asetukset koskevat kaikkia automaattisesti saapuvia liidejä"

FORM FIELDS:

1. Minne uusi liidi menee? — default_pipeline_stage_id (select)
   Options: org's pipeline stages by order_index
   First: "Käytä ensimmäistä vaihetta" (value: "")

2. Kenelle liidi osoitetaan? — default_owner_id (select)
   Options: org's active users (full name)
   First: "Ei automaattista omistajaa" (value: "")

3. Toimiala — default_industry (text input)
   Placeholder: "esim. SaaS, Rakentaminen, Terveys..."
   Helper: "Lisätään automaattisesti kaikkiin saapuviin liideihin"

4. Alue — default_region (text input)
   Placeholder: "esim. Uusimaa, Pirkanmaa, Koko Suomi..."
   Helper: "Lisätään automaattisesti kaikkiin saapuviin liideihin"

5. Oletustagit — default_tags (tag input)
   Placeholder: "b2b, saas..."
   Helper: "Nämä tagit lisätään jokaiseen saapuvaan liidiin"

LIVE PREVIEW (below fields, above save button):
Gray preview bar, updates in real time as user changes fields:
"Saapuva liidi → [vaihe] → [omistaja] → [toimiala] → [alue] → tagit: [tagit]"
Empty slots show "—"

SAVE BUTTON: "Tallenna asetukset" (primary blue, full width on mobile)
On success: show green toast "Asetukset tallennettu"

---

#### Section 3 — API-avain (ADMIN ONLY — hidden from 'user' role)

Show this section ONLY if current_user.role in ('admin', 'superadmin').
Do NOT render this section at all for role='user'.

Title: "API-avain"
Subtitle: "Käytetään automaation yhdistämiseen — älä jaa tätä muille"

Content:
- Show org's active API keys in a simple list: name + masked key (first 8 chars + ••••••••)
- "Kopioi" button per key (copies full key to clipboard via JS)
- If no API keys: show "Ei API-avaimia — luo avain API-avaimet-sivulla" with link to /settings/api-keys
- Do NOT allow creating keys here — only show existing ones

---

#### Section 4 — Automaattinen AI-pisteytys (read-only info box)

Muted card, not editable:
Title: "Automaattinen AI-pisteytys"
Content (plain text, no bullets):
Score ≥ 80 → tagi "hot" | Score ≥ 60 → tagi "warm" | Score < 30 → tagi "cold"
Subtitle: "Tagit lisätään kun AI on analysoinut liidin"

---

### ROUTE HANDLERS

GET /settings/leads:
- LeadRoutingService.get_settings(current_org.id) — auto-creates if missing
- Load pipeline stages, active users for dropdowns
- Render template

PUT /settings/leads:
- Validate pipeline_stage_id belongs to current org
- Validate owner_id belongs to current org
- Save OrgLeadSettings
- audit_log action='lead_settings_updated'
- Return JSON {"success": true} (AJAX save, no page reload)

---

## 7. NAVIGATION

In sidebar, under Asetukset section:
- Icon: ti-settings-automation (or ti-route)
- Label: "Liidiasetukset"
- URL: /settings/leads
- Show for ALL roles (admin, superadmin, user) — but API key section is hidden inside the page for non-admins
- REMOVE any existing "Liidivirrat" sidebar link pointing to the old streams list

---

## 8. EMAIL TEMPLATE — Stale alert

Template name: lead_stale_alert
Subject: "Ei uusia liidejä {{ days }} päivään — {{ org_name }}"
Variables: org_name, last_lead_at, days

HTML:
- Amber warning header
- "Viimeisin liidi saapui {{ last_lead_at }} — onko kaikki kunnossa?"
- CTA button: "Tarkista liidiasetukset" → /settings/leads
- Standard FlowLeads footer

---

## 9. TESTS

tests/test_lead_routing.py:

test_apply_routing_sets_stage:
- Lead with no stage → gets default_pipeline_stage_id from settings
- Lead with existing stage → stage is NOT overwritten (upsert safety)

test_apply_routing_sets_owner:
- Lead with no owner → gets default_owner_id
- Lead with existing owner → owner is NOT overwritten

test_apply_routing_merges_tags:
- Existing tags ['b2b'] + settings tags ['saas'] → ['b2b', 'saas']
- No duplicates if tag already exists

test_fallback_stage:
- Settings has no default stage → first pipeline stage by order_index is used
- If org has no stages at all → lead saved without stage (no crash)

test_settings_auto_created:
- New org with no OrgLeadSettings row → get_settings() creates one automatically

test_score_tagging:
- score=85 → 'hot' tag added
- score=65 → 'warm' tag added
- score=20 → 'cold' tag added
- Calling twice doesn't duplicate tags

test_cross_tenant:
- Org A's settings never applied to Org B's leads
- PUT /settings/leads validates stage and owner belong to current org

test_health_monitoring:
- last_lead_at 4 days ago + total_lead_count > 0 → flagged as stale
- last_lead_at today → not stale
- total_lead_count == 0 → not flagged (never received leads)

---

## SECURITY

- All OrgLeadSettings queries MUST include organization_id
- Route requires @require_role('admin') or @require_role('superadmin')
- pipeline_stage_id and owner_id validated against current org before saving
- Audit log on every settings change
- Health alert emails only sent to admins of the affected org

---

## WHAT NOT TO BUILD

- Multiple routing rules per org (V3 if needed)
- Source-based routing visible to customer (internal Matias-side config only)
- n8n connection instructions in customer UI (n8n is invisible to customers)
- Round-robin owner assignment (V3)
```

---

## Muutokset tiedostoihin

| Tiedosto | Muutos |
|---|---|
| `app/streams/models.py` | OrgLeadSettings-malli |
| `app/streams/services.py` | LeadRoutingService + LeadHealthService |
| `app/api/routes.py` | Routing-kutsu POST /api/v1/leads -endpointiin |
| `app/leads/services.py` | apply_score_tags() AI-rikastuksen jälkeen |
| `app/templates/streams/settings.html` | Liidiasetukset-sivu |
| `app/email/templates.py` | lead_stale_alert -pohja |
| `app/__init__.py` | APScheduler health check |
| `migrations/` | Uusi migraatio org_lead_settings-taululle |
| `tests/test_lead_routing.py` | Kaikki testit |
