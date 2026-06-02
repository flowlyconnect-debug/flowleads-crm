# FlowLeads CRM — UX-parannusvaiheet
## Hyväksytyt 5 teemaa → Cursor-promptit

**Periaate:** Kaikki muutokset ovat VAIN template/CSS/JS — ei Python-backend-muutoksia ellei erikseen mainittu.  
**Flowly-värit:** `#0B0F1A` sidebar · `#1D6BF3` primary · `#38BDF8` highlight · `#F4F6FB` bg

---

## VAIHE UX-1 — Command Center Dashboard
**Arvio:** 2–3 päivää  
**Korjaa ongelmat:** #4 (ei command center) · #12 (ei live feel) · #10 (ei system feeling) · #13 (heikko hierarkia)

### Cursor-prompt

```
Rebuild the FlowLeads CRM dashboard as a Sales Command Center.
Current problem: looks like an analytics page. Goal: feels like a war room.
DO NOT change Python backend or AnalyticsService — only templates and CSS/JS.

File: app/templates/dashboard/index.html

=== LAYOUT: 3 ZONES ===

Zone 1 — ALERT STRIP (full width, top, collapsible)
Zone 2 — MAIN GRID (65% left + 35% right)
Zone 3 — WORKFLOW CONTEXT BAR (bottom, sticky)

=== ZONE 1: ALERT STRIP ===

Show ONLY when there are actionable alerts. Hide if no alerts.

<div class="alert-strip" id="alertStrip">
  <div class="alert-strip-inner">
    <span class="alert-strip-icon">⚡</span>
    <span class="alert-strip-text">
      <strong>3 toimintoa odottaa:</strong>
      <a href="/leads?filter=no_contact_14d" class="alert-link">2 liidiä ei kontaktoitu 14+ pv</a>
      ·
      <a href="/tasks?filter=overdue" class="alert-link">1 tehtävä myöhässä</a>
      ·
      <a href="/proposals?filter=expiring" class="alert-link">1 tarjous vanhenee 2 pv</a>
    </span>
    <button class="alert-strip-close" onclick="closeAlertStrip()">✕</button>
  </div>
</div>

CSS:
.alert-strip {
  background: linear-gradient(90deg, #1D6BF3 0%, #1558D6 100%);
  padding: 10px 28px;
  margin: 0 0 16px 0;
}
.alert-strip-inner {
  display: flex; align-items: center; gap: 12px;
  max-width: 100%;
}
.alert-strip-icon { font-size: 16px; flex-shrink: 0; }
.alert-strip-text { flex: 1; font-size: 13px; color: white; }
.alert-strip-text strong { font-weight: 700; }
.alert-link {
  color: rgba(255,255,255,0.85);
  text-decoration: underline;
  text-decoration-color: rgba(255,255,255,0.4);
  font-weight: 500;
}
.alert-link:hover { color: white; }
.alert-strip-close {
  background: none; border: none; color: rgba(255,255,255,0.6);
  cursor: pointer; font-size: 16px; padding: 0 4px;
  transition: color 120ms;
}
.alert-strip-close:hover { color: white; }

Backend: add to dashboard route:
alerts = []
if leads_no_contact_14d_count > 0:
    alerts.append({'type': 'no_contact', 'count': leads_no_contact_14d_count, 'url': '/leads?filter=no_contact_14d', 'label': f'{leads_no_contact_14d_count} liidiä ei kontaktoitu 14+ pv'})
if overdue_tasks_count > 0:
    alerts.append({'type': 'overdue', 'count': overdue_tasks_count, 'url': '/tasks?filter=overdue', 'label': f'{overdue_tasks_count} tehtävää myöhässä'})
Pass alerts to template.


=== ZONE 2 LEFT: COMMAND ACTIONS (65%) ===

A. METRIC CARDS — compact 4-grid (already redesigned in UI-D1)
   Keep as-is from UI-D1.

B. "AI PULSE" CARD — THE DIFFERENTIATOR

<div class="card ai-pulse-card">
  <div class="card-header">
    <div style="display:flex;align-items:center;gap:8px;">
      <div class="ai-pulse-indicator"></div>
      <span class="card-title">AI Pulse</span>
    </div>
    <span style="font-size:11px;color:var(--text-muted);">Juuri nyt</span>
  </div>
  <div class="ai-pulse-feed" id="aiPulseFeed">
    {% for signal in ai_signals %}
    <div class="ai-pulse-item {{ signal.type }}">
      <span class="ai-pulse-icon">{{ signal.icon }}</span>
      <div class="ai-pulse-content">
        <span class="ai-pulse-text">{{ signal.text }}</span>
        <a href="{{ signal.url }}" class="ai-pulse-action">{{ signal.action }} →</a>
      </div>
      <span class="ai-pulse-time">{{ signal.time_ago }}</span>
    </div>
    {% else %}
    <div class="ai-pulse-empty">
      <p>Ei signaaleja vielä.</p>
      <p style="font-size:12px;color:var(--text-muted);">AI analysoi liidejä jatkuvasti.</p>
    </div>
    {% endfor %}
  </div>
</div>

CSS:
.ai-pulse-card {
  border: 1px solid #BFDBFE;
  background: linear-gradient(160deg, #FFFFFF 60%, #EBF2FF 100%);
}
.ai-pulse-indicator {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: #1D6BF3;
  box-shadow: 0 0 0 3px rgba(29,107,243,0.2);
  animation: pulse-ring 2s ease infinite;
}
@keyframes pulse-ring {
  0%   { box-shadow: 0 0 0 0 rgba(29,107,243,0.4); }
  70%  { box-shadow: 0 0 0 8px rgba(29,107,243,0); }
  100% { box-shadow: 0 0 0 0 rgba(29,107,243,0); }
}
.ai-pulse-feed { padding: 0 20px 16px; }
.ai-pulse-item {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid #F0F2F8;
}
.ai-pulse-item:last-child { border-bottom: none; }
.ai-pulse-icon { font-size: 16px; flex-shrink: 0; margin-top: 1px; }
.ai-pulse-content { flex: 1; }
.ai-pulse-text { font-size: 13px; color: #374151; display: block; line-height: 1.4; }
.ai-pulse-action { font-size: 12px; color: #1D6BF3; font-weight: 600; text-decoration: none; }
.ai-pulse-action:hover { text-decoration: underline; }
.ai-pulse-time { font-size: 11px; color: #9CA3AF; flex-shrink: 0; }

/* Signal types */
.ai-pulse-item.hot .ai-pulse-icon::after { content: ''; }
.ai-pulse-item.risk { background: #FFF8F8; border-radius: 6px; padding: 8px; margin: 2px -8px; }
.ai-pulse-item.hot  { background: #F0FDF4; border-radius: 6px; padding: 8px; margin: 2px -8px; }

Backend — add to dashboard route:
Generate ai_signals list:
- Leads with score > 80 created today: {'icon': '🔥', 'type': 'hot', 'text': f'Korkean potentiaalin liidi: {lead.company}', 'action': 'Avaa', 'url': f'/leads/{lead.id}', 'time_ago': '2h sitten'}
- Leads not contacted 14d: {'icon': '⚠️', 'type': 'risk', 'text': f'{lead.company} — ei kontaktia 14 päivään', 'action': 'Ota yhteyttä', 'url': f'/leads/{lead.id}'}
- Proposals viewed recently: {'icon': '👁', 'type': 'signal', 'text': f'Tarjous avattu: {lead.company}', 'action': 'Seuraa', 'url': f'/proposals/{proposal.id}'}
- High-score leads from n8n today: {'icon': '⚡', 'type': 'hot', 'text': f'n8n toi uuden liidin: {lead.company} (score {lead.score})', 'action': 'Tutki', 'url': f'/leads/{lead.id}'}
Limit to 6 most recent/important signals.


C. "PRIORITY ACTIONS" CARD — Today's execution list

<div class="card">
  <div class="card-header">
    <span class="card-title">Tehtäväsi tänään</span>
    <span class="badge badge-accent" id="todayTaskCount">{{ today_tasks|length }}</span>
  </div>
  <div class="priority-actions-list">
    {% for task in today_tasks[:5] %}
    <div class="priority-action-item" data-task-id="{{ task.id }}">
      <button class="priority-complete-btn" onclick="completeTask({{ task.id }}, this)" title="Merkitse valmiiksi">
        <span class="check-circle">○</span>
      </button>
      <div class="priority-action-content">
        <div class="priority-action-title">{{ task.title }}</div>
        <div class="priority-action-meta">
          {% if task.lead %}<a href="/leads/{{ task.lead.id }}" class="priority-lead-link">{{ task.lead.company }}</a> · {% endif %}
          <span class="{% if task.is_overdue %}text-danger{% elif task.due_today %}text-warning{% endif %}">
            {{ task.due_date.strftime('%H:%M') if task.due_date else '' }}
          </span>
        </div>
      </div>
      <span class="priority-type-badge priority-{{ task.type }}">{{ task.type }}</span>
    </div>
    {% else %}
    <div class="priority-empty">
      <span style="font-size:28px;">🎉</span>
      <p style="font-size:13px;color:var(--text-secondary);margin-top:8px;">Ei tehtäviä tänään — olet ajan tasalla!</p>
    </div>
    {% endfor %}
    {% if today_tasks|length > 5 %}
    <a href="/tasks" class="priority-show-more">+ {{ today_tasks|length - 5 }} lisää tehtävää</a>
    {% endif %}
  </div>
</div>

CSS:
.priority-actions-list { padding: 0 20px 16px; }
.priority-action-item {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid #F0F2F8;
  transition: background 120ms;
}
.priority-action-item:last-child { border-bottom: none; }
.priority-action-item.completed { opacity: 0.4; }
.priority-action-item.completing { animation: complete-task 400ms ease forwards; }
@keyframes complete-task {
  0%   { transform: scaleX(1); opacity: 1; }
  50%  { transform: scaleX(1.02); }
  100% { transform: scaleX(0); opacity: 0; max-height: 0; padding: 0; }
}

.priority-complete-btn {
  background: none; border: none; cursor: pointer;
  width: 24px; height: 24px;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.check-circle {
  font-size: 18px;
  color: #D1D5DB;
  transition: color 120ms;
  line-height: 1;
}
.priority-complete-btn:hover .check-circle { color: #1D6BF3; }
.priority-complete-btn.done .check-circle { color: #10B981; }

.priority-action-content { flex: 1; min-width: 0; }
.priority-action-title { font-size: 13px; font-weight: 500; color: #0F1117; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.priority-action-meta { font-size: 11px; color: #9CA3AF; margin-top: 2px; }
.priority-lead-link { color: #1D6BF3; text-decoration: none; }
.priority-lead-link:hover { text-decoration: underline; }

.priority-type-badge { font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 20px; flex-shrink: 0; }
.priority-call   { background: #D1FAE5; color: #065F46; }
.priority-email  { background: #DBEAFE; color: #1E40AF; }
.priority-follow_up { background: #FEF3C7; color: #92400E; }
.priority-meeting { background: #EDE9FE; color: #5B21B6; }

.priority-show-more { display: block; text-align: center; padding: 10px; font-size: 12px; color: #1D6BF3; font-weight: 500; text-decoration: none; }
.priority-empty { text-align: center; padding: 24px 0; }


=== ZONE 2 RIGHT: ACTIVITY STREAM (35%) ===

<div class="card activity-stream-card">
  <div class="card-header">
    <span class="card-title">Live aktiviteetti</span>
    <span class="activity-live-dot"></span>
  </div>
  <div class="activity-stream" id="activityStream">
    {% for activity in recent_activities %}
    <div class="activity-stream-item">
      <div class="activity-stream-icon {{ activity.type }}">{{ activity.icon }}</div>
      <div class="activity-stream-content">
        <div class="activity-stream-text">
          <strong>{{ activity.subject }}</strong> — {{ activity.description }}
        </div>
        <div class="activity-stream-meta">{{ activity.time_ago }}</div>
      </div>
    </div>
    {% endfor %}
  </div>
</div>

CSS:
.activity-live-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: #10B981;
  animation: pulse-ring 2s ease infinite;
}
.activity-stream { padding: 0 20px 16px; max-height: 420px; overflow-y: auto; }
.activity-stream-item {
  display: flex; gap: 10px;
  padding: 9px 0;
  border-bottom: 1px solid #F0F2F8;
}
.activity-stream-item:last-child { border-bottom: none; }
.activity-stream-icon {
  width: 28px; height: 28px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; flex-shrink: 0;
}
.activity-stream-icon.email_sent  { background: #DBEAFE; }
.activity-stream-icon.stage_changed { background: #D1FAE5; }
.activity-stream-icon.lead_created { background: #EDE9FE; }
.activity-stream-icon.ai_enriched  { background: #EBF2FF; }
.activity-stream-icon.task_completed { background: #D1FAE5; }
.activity-stream-text { font-size: 12px; color: #374151; line-height: 1.4; }
.activity-stream-text strong { color: #0F1117; font-weight: 600; }
.activity-stream-meta { font-size: 11px; color: #9CA3AF; margin-top: 2px; }

Auto-refresh activity stream every 30 seconds:
setInterval(() => {
  fetch('/api/dashboard/activity-stream')
    .then(r => r.json())
    .then(data => {
      if (data.success) updateActivityStream(data.data.activities);
    });
}, 30000);

Backend: add route GET /api/dashboard/activity-stream
Returns last 20 activities across org as JSON.
Add to existing AnalyticsService or create simple query:
activities = Activity.query.filter_by(organization_id=g.org.id)
  .order_by(Activity.created_at.desc()).limit(20).all()


=== TASK COMPLETE VIA AJAX ===

function completeTask(taskId, btn) {
  fetch(`/tasks/${taskId}/complete`, {
    method: 'POST',
    headers: {'X-CSRFToken': window.csrfToken}
  }).then(r => r.json()).then(data => {
    if (data.success) {
      const item = btn.closest('.priority-action-item');
      item.classList.add('completing');
      setTimeout(() => item.remove(), 400);
      // Decrement badge
      const badge = document.getElementById('todayTaskCount');
      badge.textContent = Math.max(0, parseInt(badge.textContent) - 1);
    }
  });
}


=== WORKFLOW CONTEXT BAR (bottom of every page) ===

Add to base.html, inside .app-main, after main content:

<div class="workflow-context-bar">
  <span class="wf-label">Workflow:</span>
  <div class="wf-steps">
    <a href="/leads?filter=new" class="wf-step {% if workflow_stats.new_leads > 0 %}has-data{% endif %}">
      <span class="wf-step-count">{{ workflow_stats.new_leads }}</span>
      <span class="wf-step-name">Uudet liidit</span>
    </a>
    <span class="wf-arrow">→</span>
    <a href="/tasks" class="wf-step {% if workflow_stats.pending_tasks > 0 %}has-data{% endif %}">
      <span class="wf-step-count">{{ workflow_stats.pending_tasks }}</span>
      <span class="wf-step-name">Tehtävät</span>
    </a>
    <span class="wf-arrow">→</span>
    <a href="/proposals?filter=active" class="wf-step {% if workflow_stats.active_proposals > 0 %}has-data{% endif %}">
      <span class="wf-step-count">{{ workflow_stats.active_proposals }}</span>
      <span class="wf-step-name">Tarjoukset</span>
    </a>
    <span class="wf-arrow">→</span>
    <a href="/leads?filter=won" class="wf-step success">
      <span class="wf-step-count">{{ workflow_stats.won_this_month }}</span>
      <span class="wf-step-name">Voitettu kk</span>
    </a>
  </div>
</div>

CSS:
.workflow-context-bar {
  position: sticky; bottom: 0;
  background: white;
  border-top: 1px solid #E4E7EF;
  padding: 10px 28px;
  display: flex; align-items: center; gap: 16px;
  box-shadow: 0 -2px 8px rgba(0,0,0,0.04);
  z-index: 10;
}
.wf-label { font-size: 10px; font-weight: 700; color: #9CA3AF; text-transform: uppercase; letter-spacing: 0.06em; flex-shrink: 0; }
.wf-steps { display: flex; align-items: center; gap: 8px; }
.wf-step { display: flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 20px; text-decoration: none; transition: all 120ms; border: 1px solid transparent; }
.wf-step:hover { background: #EBF2FF; border-color: #BFDBFE; }
.wf-step.has-data { background: #EBF2FF; border-color: #BFDBFE; }
.wf-step.success { background: #D1FAE5; border-color: #6EE7B7; }
.wf-step-count { font-size: 14px; font-weight: 800; color: #1D6BF3; }
.wf-step.success .wf-step-count { color: #059669; }
.wf-step-name { font-size: 11px; color: #6B7280; font-weight: 500; }
.wf-arrow { color: #D1D5DB; font-size: 14px; }

Backend: add workflow_stats to every template context via @app.context_processor
```

### ✅ Hyväksymiskriteerit
- [ ] Alert strip näkyy kun on myöhässä olevia tehtäviä/liidejä
- [ ] AI Pulse -kortti näyttää reaaliaikaiset signaalit (hot leads, riskit)
- [ ] Tehtäväkortti: complete toimii AJAXilla ilman sivunlatausta, animaatio
- [ ] Activity stream päivittyy 30s välein
- [ ] Workflow context bar näkyy jokaisen sivun alaosassa
- [ ] Kaikki linkit ohjaavat oikeisiin suodatuksiin

---

## VAIHE UX-2 — AI-Native Identity
**Arvio:** 2 päivää  
**Korjaa ongelmat:** #3 (ei actionable intel) · #11 (AI ei näy) · #15 (ei differentiator)

### Cursor-prompt

```
Add AI-native identity to FlowLeads CRM — AI recommendations visible in context
throughout the app, not as a separate page. DO NOT change AI enrichment backend.

=== 1. FLOATING AI ASSISTANT BUTTON (all pages) ===

Add to base.html before </body>:

<button class="ai-fab" id="aiFab" onclick="toggleAIDrawer()" title="AI-avustaja">
  <span class="ai-fab-icon">✦</span>
  <span class="ai-fab-label">AI</span>
  <span class="ai-fab-badge" id="aiFabBadge" style="display:none">3</span>
</button>

<div class="ai-drawer" id="aiDrawer">
  <div class="ai-drawer-header">
    <div style="display:flex;align-items:center;gap:10px;">
      <div class="ai-pulse-indicator"></div>
      <span style="font-size:14px;font-weight:700;color:#0F1117;">FlowLeads AI</span>
    </div>
    <button onclick="toggleAIDrawer()" style="background:none;border:none;cursor:pointer;color:#9CA3AF;font-size:18px;">✕</button>
  </div>
  <div class="ai-drawer-body" id="aiDrawerBody">
    <div class="ai-drawer-loading">
      <div class="skeleton" style="height:60px;border-radius:8px;margin-bottom:10px;"></div>
      <div class="skeleton" style="height:60px;border-radius:8px;margin-bottom:10px;"></div>
      <div class="skeleton" style="height:60px;border-radius:8px;"></div>
    </div>
  </div>
</div>
<div class="ai-drawer-overlay" id="aiOverlay" onclick="toggleAIDrawer()"></div>

CSS:
.ai-fab {
  position: fixed; bottom: 80px; right: 20px;
  background: linear-gradient(135deg, #1D6BF3, #0EA5E9);
  color: white;
  border: none; border-radius: 50px;
  padding: 12px 18px;
  display: flex; align-items: center; gap: 7px;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(29,107,243,0.40);
  font-size: 13px; font-weight: 700;
  transition: all 200ms;
  z-index: 200;
}
.ai-fab:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(29,107,243,0.50);
}
.ai-fab-icon { font-size: 16px; }
.ai-fab-badge {
  position: absolute; top: -4px; right: -4px;
  background: #EF4444; color: white;
  font-size: 10px; font-weight: 800;
  width: 18px; height: 18px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  border: 2px solid white;
}

.ai-drawer {
  position: fixed; right: -360px; top: 0; bottom: 0;
  width: 340px;
  background: white;
  border-left: 1px solid #E4E7EF;
  box-shadow: -8px 0 32px rgba(0,0,0,0.12);
  z-index: 300;
  display: flex; flex-direction: column;
  transition: right 300ms cubic-bezier(0.4, 0, 0.2, 1);
}
.ai-drawer.open { right: 0; }

.ai-drawer-overlay {
  display: none;
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.20);
  z-index: 290;
}
.ai-drawer-overlay.visible { display: block; }

.ai-drawer-header {
  padding: 16px 20px;
  border-bottom: 1px solid #E4E7EF;
  display: flex; align-items: center; justify-content: space-between;
  background: linear-gradient(135deg, #EBF2FF, #F4F6FB);
}
.ai-drawer-body { flex: 1; overflow-y: auto; padding: 16px 20px; }

.ai-recommendation {
  background: #F4F6FB;
  border: 1px solid #E4E7EF;
  border-left: 3px solid #1D6BF3;
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 10px;
}
.ai-recommendation.urgent { border-left-color: #EF4444; background: #FFF8F8; }
.ai-recommendation.hot    { border-left-color: #10B981; background: #F0FDF4; }

.ai-rec-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.ai-rec-icon   { font-size: 16px; }
.ai-rec-title  { font-size: 13px; font-weight: 700; color: #0F1117; }
.ai-rec-body   { font-size: 12px; color: #5C6170; line-height: 1.5; margin-bottom: 8px; }
.ai-rec-action { font-size: 12px; font-weight: 600; color: #1D6BF3; text-decoration: none; }
.ai-rec-action:hover { text-decoration: underline; }

JavaScript:
function toggleAIDrawer() {
  const drawer = document.getElementById('aiDrawer');
  const overlay = document.getElementById('aiOverlay');
  const isOpen = drawer.classList.contains('open');

  drawer.classList.toggle('open', !isOpen);
  overlay.classList.toggle('visible', !isOpen);

  if (!isOpen) {
    loadAIRecommendations();
  }
}

function loadAIRecommendations() {
  fetch('/api/ai/recommendations?context=' + window.aiContext)
    .then(r => r.json())
    .then(data => {
      const body = document.getElementById('aiDrawerBody');
      if (data.success && data.data.recommendations.length > 0) {
        body.innerHTML = data.data.recommendations.map(rec => `
          <div class="ai-recommendation ${rec.priority}">
            <div class="ai-rec-header">
              <span class="ai-rec-icon">${rec.icon}</span>
              <span class="ai-rec-title">${rec.title}</span>
            </div>
            <div class="ai-rec-body">${rec.body}</div>
            ${rec.action_url ? `<a href="${rec.action_url}" class="ai-rec-action">${rec.action_label} →</a>` : ''}
          </div>
        `).join('');
      } else {
        body.innerHTML = '<div style="text-align:center;padding:32px;color:#9CA3AF;font-size:13px;">Kaikki näyttää hyvältä ✓</div>';
      }
    });
}

// Set context per page (add to each page template):
// <script>window.aiContext = 'dashboard';</script>
// <script>window.aiContext = 'pipeline';</script>
// <script>window.aiContext = 'leads';</script>

Backend: add route GET /api/ai/recommendations?context=X
Returns context-aware recommendations:
- dashboard: top 5 priority actions (overdue tasks, no-contact leads, expiring proposals)
- pipeline: leads at risk (stale in stage), high-probability leads to push
- leads: leads without tasks, leads with high score but no contact
- proposals: proposals viewed but not accepted, proposals expiring
Format: [{'icon': '🔥', 'priority': 'hot', 'title': '...', 'body': '...', 'action_url': '...', 'action_label': '...'}]
This is RULE-BASED (no OpenAI call) — just smart queries. Fast and free.


=== 2. ACTION BADGES ON PIPELINE CARDS ===

Add to every lead card in pipeline (app/templates/leads/pipeline.html):

{% set urgency = namespace(badge='', css='') %}
{% if lead.last_contacted_at and (now - lead.last_contacted_at).days > 14 %}
  {% set urgency.badge = '⚠ ' + ((now - lead.last_contacted_at).days)|string + ' pv hiljaa' %}
  {% set urgency.css = 'badge-urgency-risk' %}
{% elif lead.ai_enrichment_status == 'completed' and lead.score and lead.score > 80 %}
  {% set urgency.badge = '🔥 Kuuma liidi' %}
  {% set urgency.css = 'badge-urgency-hot' %}
{% elif lead.sequence_active %}
  {% set urgency.badge = '✉ Sekvenssissä' %}
  {% set urgency.css = 'badge-urgency-sequence' %}
{% elif lead.open_tasks_count > 0 %}
  {% set urgency.badge = '📋 ' + lead.open_tasks_count|string + ' tehtävää' %}
  {% set urgency.css = 'badge-urgency-task' %}
{% endif %}

{% if urgency.badge %}
<div class="lead-urgency-badge {{ urgency.css }}">{{ urgency.badge }}</div>
{% endif %}

CSS (add to pipeline.css):
.lead-urgency-badge {
  font-size: 10px; font-weight: 700;
  padding: 3px 7px;
  border-radius: 4px;
  margin-top: 6px;
  display: inline-block;
}
.badge-urgency-risk     { background: #FEE2E2; color: #991B1B; }
.badge-urgency-hot      { background: #D1FAE5; color: #065F46; }
.badge-urgency-sequence { background: #DBEAFE; color: #1E40AF; }
.badge-urgency-task     { background: #FEF3C7; color: #92400E; }

Also add AI next action suggestion to card footer:
{% if lead.ai_recommendation %}
<div class="lead-next-action">→ {{ lead.ai_recommendation }}</div>
{% endif %}

.lead-next-action {
  font-size: 11px; color: #1D6BF3; font-weight: 500;
  margin-top: 5px; font-style: italic;
}

Backend: add lead.ai_recommendation property (rule-based, not OpenAI):
  - If no contact 14d: "Ota yhteyttä nyt"
  - If score > 80 and no proposal: "Lähetä tarjous"
  - If proposal sent 7d ago and not viewed: "Muistuta tarjouksesta"
  - If proposal viewed 3+ times: "Seuraa välittömästi"


=== 3. AI SCORE VISUAL ON LEAD CARDS ===

Replace plain score number with visual score bar:

<div class="ai-score-bar-wrapper" title="AI Score: {{ lead.score }}/100">
  <div class="ai-score-bar" style="width: {{ lead.score or 0 }}%;"
       class="{% if lead.score >= 70 %}score-high{% elif lead.score >= 40 %}score-mid{% else %}score-low{% endif %}">
  </div>
  <span class="ai-score-num">{{ lead.score or '—' }}</span>
</div>

CSS:
.ai-score-bar-wrapper {
  display: flex; align-items: center; gap: 6px;
  margin-top: 4px;
}
.ai-score-bar {
  height: 4px; border-radius: 2px; flex: 0 0 auto;
  transition: width 300ms ease;
  max-width: 60px;
}
.score-high { background: #10B981; }
.score-mid  { background: #F59E0B; }
.score-low  { background: #EF4444; }
.ai-score-num { font-size: 11px; font-weight: 700; color: #6B7280; }
```

### ✅ Hyväksymiskriteerit
- [ ] AI FAB-painike näkyy oikeassa alakulmassa joka sivulla
- [ ] Drawer avautuu slide-in animaatiolla
- [ ] Suositukset ovat kontekstuaalisia (pipeline vs. dashboard eri sisältö)
- [ ] Pipeline-kortit näyttävät urgency-badget (⚠ hiljaa, 🔥 kuuma)
- [ ] AI-suositukset ovat rule-based (ei OpenAI-kutsu joka kerralla)
- [ ] Score-bar visualisoi pisteytyksen visuaalisesti

---

## VAIHE UX-3 — Smart Pipeline & Leads
**Arvio:** 2 päivää  
**Korjaa ongelmat:** #5 (hiljaiset kortit) · #6 (table-heavy) · #14 (liikaa navigointia)

### Cursor-prompt

```
Add smart interactions to pipeline and leads views. Key additions:
1. Richer pipeline cards with engagement signals
2. Lead preview drawer (slide-in, no page navigation)  
3. Command Palette (Cmd+K)
DO NOT change backend models or services.

=== 1. ENRICHED PIPELINE CARD (update to existing card) ===

Add these elements to the existing lead card in app/templates/leads/pipeline.html:

After contact name/company, add ENGAGEMENT ROW:

<div class="card-engagement-row">
  <!-- Last activity indicator -->
  {% if lead.last_activity %}
    {% set days_ago = (now - lead.last_activity.created_at).days %}
    <span class="engagement-dot
      {% if days_ago == 0 %}eng-today
      {% elif days_ago <= 3 %}eng-recent
      {% elif days_ago <= 14 %}eng-aging
      {% else %}eng-cold{% endif %}"
      title="Viimeisin aktiviteetti: {{ days_ago }} pv sitten">
    </span>
    <span class="engagement-label">
      {% if days_ago == 0 %}Tänään{% elif days_ago == 1 %}Eilen
      {% elif days_ago <= 7 %}{{ days_ago }} pv sitten{% else %}{{ days_ago }} pv hiljaa{% endif %}
    </span>
  {% else %}
    <span class="engagement-dot eng-cold"></span>
    <span class="engagement-label" style="color:#EF4444;">Ei aktiviteettia</span>
  {% endif %}

  <!-- Engagement level dots (3 dots = active, 1 = cold) -->
  <div class="eng-dots" title="Aktiivisuustaso">
    {% for i in range(3) %}
      <span class="eng-dot {% if i < lead.engagement_level %}filled{% endif %}"></span>
    {% endfor %}
  </div>
</div>

CSS:
.card-engagement-row {
  display: flex; align-items: center; gap: 6px;
  padding-top: 7px; margin-top: 7px;
  border-top: 1px solid #F0F2F8;
  font-size: 11px; color: #9CA3AF;
}
.engagement-dot {
  width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0;
}
.eng-today  { background: #10B981; box-shadow: 0 0 0 2px rgba(16,185,129,0.25); }
.eng-recent { background: #38BDF8; }
.eng-aging  { background: #F59E0B; }
.eng-cold   { background: #EF4444; }
.engagement-label { flex: 1; }
.eng-dots { display: flex; gap: 3px; margin-left: auto; }
.eng-dot { width: 5px; height: 5px; border-radius: 50%; background: #E5E7EB; }
.eng-dot.filled { background: #1D6BF3; }

Backend: add lead.engagement_level property (0-3):
  0 = no activity
  1 = activity > 14 days ago
  2 = activity 3-14 days ago
  3 = activity within 3 days
Add lead.last_activity relationship or property.


=== 2. LEAD PREVIEW DRAWER ===

Add to base.html (before </body>):

<div class="lead-drawer" id="leadDrawer">
  <div class="lead-drawer-header">
    <div id="drawerLeadName" style="font-size:16px;font-weight:700;color:#0F1117;"></div>
    <div style="display:flex;gap:8px;">
      <a id="drawerOpenFull" href="#" class="btn btn-secondary btn-sm">Avaa täysin →</a>
      <button onclick="closeLeadDrawer()" style="background:none;border:none;cursor:pointer;color:#9CA3AF;font-size:20px;">✕</button>
    </div>
  </div>
  <div class="lead-drawer-body" id="leadDrawerBody">
    <div class="skeleton" style="height:80px;border-radius:8px;margin-bottom:12px;"></div>
    <div class="skeleton" style="height:200px;border-radius:8px;margin-bottom:12px;"></div>
    <div class="skeleton" style="height:120px;border-radius:8px;"></div>
  </div>
</div>
<div class="lead-drawer-overlay" id="leadDrawerOverlay" onclick="closeLeadDrawer()"></div>

CSS:
.lead-drawer {
  position: fixed; right: -480px; top: 0; bottom: 0;
  width: 460px; background: white;
  border-left: 1px solid #E4E7EF;
  box-shadow: -8px 0 32px rgba(0,0,0,0.12);
  z-index: 400; display: flex; flex-direction: column;
  transition: right 280ms cubic-bezier(0.4, 0, 0.2, 1);
}
.lead-drawer.open { right: 0; }
.lead-drawer-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.2); z-index:390; }
.lead-drawer-overlay.visible { display:block; }
.lead-drawer-header {
  padding: 16px 20px;
  border-bottom: 1px solid #E4E7EF;
  display: flex; align-items: center; justify-content: space-between;
  flex-shrink: 0;
}
.lead-drawer-body { flex:1; overflow-y:auto; padding:20px; }

JavaScript:
function openLeadDrawer(leadId) {
  const drawer = document.getElementById('leadDrawer');
  const overlay = document.getElementById('leadDrawerOverlay');
  drawer.classList.add('open');
  overlay.classList.add('visible');
  document.getElementById('drawerOpenFull').href = `/leads/${leadId}`;

  fetch(`/api/leads/${leadId}/preview`)
    .then(r => r.json())
    .then(data => {
      if (data.success) renderLeadDrawer(data.data);
    });
}
function closeLeadDrawer() {
  document.getElementById('leadDrawer').classList.remove('open');
  document.getElementById('leadDrawerOverlay').classList.remove('visible');
}
function renderLeadDrawer(lead) {
  document.getElementById('drawerLeadName').textContent = `${lead.first_name} ${lead.last_name} — ${lead.company}`;
  document.getElementById('leadDrawerBody').innerHTML = `
    <div class="drawer-contact-section">
      <div class="drawer-field"><label>Yritys</label><span>${lead.company || '—'}</span></div>
      <div class="drawer-field"><label>Sähköposti</label><a href="mailto:${lead.email}">${lead.email}</a></div>
      <div class="drawer-field"><label>Puhelin</label><span>${lead.phone || '—'}</span></div>
      <div class="drawer-field"><label>Vaihe</label><span class="badge">${lead.stage_name}</span></div>
      <div class="drawer-field"><label>AI Score</label><span>${lead.score || '—'}/100</span></div>
    </div>
    ${lead.ai_summary ? `<div class="drawer-ai-summary"><span class="drawer-ai-icon">✦</span>${lead.ai_summary}</div>` : ''}
    <div class="drawer-quick-actions">
      <a href="/leads/${lead.id}/email/compose" class="btn btn-primary btn-sm">✉ Sähköposti</a>
      <a href="/leads/${lead.id}/tasks/new" class="btn btn-secondary btn-sm">+ Tehtävä</a>
      <a href="/leads/${lead.id}/email/compose?proposal=true" class="btn btn-secondary btn-sm">📄 Tarjous</a>
    </div>
    <div class="drawer-activities">
      <div class="drawer-section-title">Viimeisin aktiviteetti</div>
      ${lead.recent_activities.map(a => `
        <div class="drawer-activity-item">
          <span class="drawer-activity-icon">${a.icon}</span>
          <div><div class="drawer-activity-text">${a.description}</div><div class="drawer-activity-time">${a.time_ago}</div></div>
        </div>
      `).join('')}
    </div>
  `;
}

CSS for drawer content:
.drawer-contact-section { background: #F4F6FB; border-radius: 10px; padding: 14px; margin-bottom: 14px; }
.drawer-field { display: flex; justify-content: space-between; padding: 5px 0; font-size: 13px; border-bottom: 1px solid #F0F2F8; }
.drawer-field:last-child { border-bottom: none; }
.drawer-field label { color: #9CA3AF; font-size: 11px; font-weight: 600; text-transform: uppercase; }
.drawer-ai-summary { background: linear-gradient(135deg, #EBF2FF, #F4F6FB); border: 1px solid #BFDBFE; border-radius: 8px; padding: 12px; font-size: 12px; color: #374151; margin-bottom: 14px; display: flex; gap: 8px; line-height: 1.5; }
.drawer-ai-icon { color: #1D6BF3; font-size: 14px; flex-shrink: 0; }
.drawer-quick-actions { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.drawer-section-title { font-size: 11px; font-weight: 700; color: #9CA3AF; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px; }
.drawer-activity-item { display: flex; gap: 8px; padding: 7px 0; border-bottom: 1px solid #F0F2F8; font-size: 12px; }
.drawer-activity-icon { font-size: 14px; flex-shrink: 0; }
.drawer-activity-text { color: #374151; }
.drawer-activity-time { color: #9CA3AF; font-size: 11px; margin-top: 1px; }

Backend: add GET /api/leads/<id>/preview
Returns compact lead data: contact info, stage, score, ai_summary, recent 5 activities.

Modify lead list rows: add onclick="openLeadDrawer({{ lead.id }})" to each row.
Keep full-page link as "Avaa täysin →" in drawer header.


=== 3. COMMAND PALETTE (Cmd+K) ===

Add to base.html:

<div class="cmd-palette-overlay" id="cmdOverlay" onclick="closeCmdPalette()" style="display:none;"></div>
<div class="cmd-palette" id="cmdPalette" style="display:none;">
  <div class="cmd-input-wrapper">
    <span class="cmd-icon">⌘</span>
    <input type="text" class="cmd-input" id="cmdInput"
           placeholder="Hae liidit, tehtävät, komennot..."
           oninput="searchCmd(this.value)"
           onkeydown="handleCmdKey(event)">
  </div>
  <div class="cmd-results" id="cmdResults">
    <div class="cmd-section-label">Nopeat toiminnot</div>
    <div class="cmd-item" onclick="window.location='/leads/new'">
      <span class="cmd-item-icon">+</span>
      <span class="cmd-item-label">Lisää uusi liidi</span>
      <span class="cmd-item-shortcut">N</span>
    </div>
    <div class="cmd-item" onclick="window.location='/tasks/new'">
      <span class="cmd-item-icon">✓</span>
      <span class="cmd-item-label">Lisää tehtävä</span>
      <span class="cmd-item-shortcut">T</span>
    </div>
    <div class="cmd-item" onclick="window.location='/pipeline'">
      <span class="cmd-item-icon">⬜</span>
      <span class="cmd-item-label">Avaa pipeline</span>
    </div>
    <div class="cmd-item" onclick="window.location='/dashboard'">
      <span class="cmd-item-icon">📊</span>
      <span class="cmd-item-label">Dashboard</span>
    </div>
  </div>
</div>

CSS:
.cmd-palette-overlay { position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:500;backdrop-filter:blur(2px); }
.cmd-palette {
  position: fixed;
  top: 20%;
  left: 50%;
  transform: translateX(-50%);
  width: 560px;
  background: white;
  border-radius: 14px;
  box-shadow: 0 24px 64px rgba(0,0,0,0.20);
  z-index: 501;
  overflow: hidden;
  border: 1px solid #E4E7EF;
}
.cmd-input-wrapper { display:flex;align-items:center;gap:10px;padding:14px 18px;border-bottom:1px solid #E4E7EF; }
.cmd-icon { font-size:16px;color:#9CA3AF; }
.cmd-input { flex:1;border:none;outline:none;font-size:15px;color:#0F1117; }
.cmd-results { max-height:360px;overflow-y:auto;padding:8px; }
.cmd-section-label { font-size:10px;font-weight:700;color:#9CA3AF;text-transform:uppercase;letter-spacing:0.06em;padding:8px 10px 4px; }
.cmd-item {
  display:flex;align-items:center;gap:10px;
  padding:9px 10px;border-radius:7px;cursor:pointer;
  transition:background 100ms;
}
.cmd-item:hover,.cmd-item.selected { background:#EBF2FF; }
.cmd-item-icon { width:24px;height:24px;border-radius:6px;background:#F3F4F6;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0; }
.cmd-item-label { flex:1;font-size:13px;font-weight:500;color:#0F1117; }
.cmd-item-shortcut { font-size:10px;background:#F3F4F6;padding:2px 6px;border-radius:4px;color:#6B7280;font-family:monospace; }
.cmd-item-meta { font-size:11px;color:#9CA3AF; }

JavaScript:
document.addEventListener('keydown', e => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    openCmdPalette();
  }
  if (e.key === 'Escape') closeCmdPalette();
});
function openCmdPalette() {
  document.getElementById('cmdPalette').style.display = 'block';
  document.getElementById('cmdOverlay').style.display = 'block';
  setTimeout(() => document.getElementById('cmdInput').focus(), 50);
}
function closeCmdPalette() {
  document.getElementById('cmdPalette').style.display = 'none';
  document.getElementById('cmdOverlay').style.display = 'none';
  document.getElementById('cmdInput').value = '';
}
function searchCmd(query) {
  if (query.length < 2) return;
  fetch(`/api/search?q=${encodeURIComponent(query)}`)
    .then(r => r.json())
    .then(data => renderCmdResults(data.data));
}
function renderCmdResults(results) {
  const el = document.getElementById('cmdResults');
  el.innerHTML = results.leads.map(l => `
    <div class="cmd-item" onclick="openLeadDrawer(${l.id});closeCmdPalette()">
      <span class="cmd-item-icon">👤</span>
      <span class="cmd-item-label">${l.company} — ${l.first_name} ${l.last_name}</span>
      <span class="cmd-item-meta">${l.stage_name}</span>
    </div>
  `).join('') + results.tasks.map(t => `
    <div class="cmd-item" onclick="window.location='/tasks';closeCmdPalette()">
      <span class="cmd-item-icon">✓</span>
      <span class="cmd-item-label">${t.title}</span>
      <span class="cmd-item-meta">${t.due_label}</span>
    </div>
  `).join('');
}

Backend: add GET /api/search?q=X
Search leads by company/name/email + tasks by title.
Return: {'leads': [...], 'tasks': [...]} max 5 each.
```

### ✅ Hyväksymiskriteerit
- [ ] Pipeline-kortit näyttävät engagement-pisteytyksen (piste + värikoodi)
- [ ] Viimeisin aktiviteetti näkyy kortissa ("Tänään" / "3 pv sitten" / "14 pv hiljaa")
- [ ] Listarivin klikkaus avaa drawer (ei sivunlataus)
- [ ] Drawer näyttää yhteystiedot, AI summary, quick actions
- [ ] Cmd+K avaa command paletten
- [ ] Command palette hakee liidejä ja tehtäviä reaaliajassa

---

## VAIHE UX-4 — Execution Workflow Pages
**Arvio:** 2 päivää  
**Korjaa ongelmat:** #7 (sekvenssit steriilit) · #8 (tarjoukset placeholder) · #9 (tehtävät lomake)

### Cursor-prompt

```
Redesign tasks, proposals, and sequences pages as execution-focused workspaces.
DO NOT change backend models — only templates and CSS/JS.

=== 1. TASKS: FOCUS MODE + EXECUTION CENTER ===

File: app/templates/tasks/index.html

TOP: FOCUS CARD (biggest visual element on page)
Shows the single most urgent task:

<div class="focus-card {% if focus_task and focus_task.is_overdue %}focus-overdue{% elif focus_task and focus_task.due_today %}focus-today{% endif %}">
  {% if focus_task %}
  <div class="focus-card-label">
    {% if focus_task.is_overdue %}⚠ MYÖHÄSSÄ{% else %}▶ TEE SEURAAVAKSI{% endif %}
  </div>
  <div class="focus-card-title">{{ focus_task.title }}</div>
  <div class="focus-card-meta">
    {% if focus_task.lead %}<a href="#" onclick="openLeadDrawer({{ focus_task.lead.id }});return false;" class="focus-lead-link">{{ focus_task.lead.company }}</a>{% endif %}
    {% if focus_task.due_date %} · {{ focus_task.due_date.strftime('%d.%m. klo %H:%M') }}{% endif %}
  </div>
  <div class="focus-card-actions">
    <button class="btn btn-primary" onclick="completeTask({{ focus_task.id }}, this, true)">
      ✓ Merkitse valmiiksi
    </button>
    <button class="btn btn-secondary" onclick="snoozeTask({{ focus_task.id }})">
      ⏰ Siirrä 1h
    </button>
    {% if focus_task.lead %}
    <a href="/leads/{{ focus_task.lead.id }}/email/compose" class="btn btn-secondary">✉ Sähköposti</a>
    {% endif %}
  </div>
  {% else %}
  <div style="text-align:center;padding:8px 0;">
    <span style="font-size:32px;">🎉</span>
    <div class="focus-card-title" style="font-size:18px;margin-top:12px;">Kaikki tehtävät hoidettu!</div>
    <div class="focus-card-meta">Loistavaa työtä. Tänään ei ole avoimia tehtäviä.</div>
  </div>
  {% endif %}
</div>

CSS:
.focus-card {
  background: linear-gradient(135deg, #EBF2FF 0%, #F4F6FB 100%);
  border: 1.5px solid #BFDBFE;
  border-radius: 14px;
  padding: 24px 28px;
  margin-bottom: 20px;
  position: relative;
  overflow: hidden;
}
.focus-card::before {
  content: '';
  position: absolute; left: 0; top: 0; bottom: 0;
  width: 5px;
  background: #1D6BF3;
}
.focus-card.focus-overdue { background: linear-gradient(135deg,#FEF2F2,#FFF8F8); border-color: #FCA5A5; }
.focus-card.focus-overdue::before { background: #EF4444; }
.focus-card.focus-today::before { background: #F59E0B; }
.focus-card.focus-today { background: linear-gradient(135deg,#FFFBEB,#FEFCE8); border-color: #FDE68A; }

.focus-card-label { font-size: 10px; font-weight: 800; color: #1D6BF3; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
.focus-card.focus-overdue .focus-card-label { color: #EF4444; }
.focus-card.focus-today .focus-card-label { color: #D97706; }
.focus-card-title { font-size: 20px; font-weight: 700; color: #0F1117; margin-bottom: 8px; line-height: 1.3; }
.focus-card-meta { font-size: 13px; color: #6B7280; margin-bottom: 16px; }
.focus-lead-link { color: #1D6BF3; text-decoration: none; font-weight: 500; }
.focus-card-actions { display: flex; gap: 10px; flex-wrap: wrap; }


TASK LIST BELOW (compact, not table):

<div class="task-queue">
  {% for task in queued_tasks %}
  <div class="task-queue-item priority-{{ task.priority }}" data-task-id="{{ task.id }}">
    <button class="task-check" onclick="completeTask({{ task.id }}, this)">○</button>
    <div class="task-queue-content">
      <div class="task-queue-title">{{ task.title }}</div>
      <div class="task-queue-meta">
        {% if task.lead %}<span class="task-lead">{{ task.lead.company }}</span>{% endif %}
        <span class="task-type-dot task-{{ task.type }}"></span>
        <span class="task-due {% if task.is_overdue %}overdue{% endif %}">
          {% if task.is_overdue %}⚠ Myöhässä{% elif task.due_today %}Tänään {{ task.due_date.strftime('%H:%M') }}{% else %}{{ task.due_date.strftime('%d.%m.') if task.due_date else '' }}{% endif %}
        </span>
      </div>
    </div>
    <div class="task-queue-actions">
      <a href="/leads/{{ task.lead.id if task.lead else '' }}" class="task-action-link" title="Avaa liidi">→</a>
    </div>
  </div>
  {% endfor %}
</div>

CSS:
.task-queue-item {
  display: flex; align-items: center; gap: 12px;
  padding: 11px 16px;
  border-radius: 8px;
  margin-bottom: 4px;
  background: white;
  border: 1px solid #E4E7EF;
  transition: all 120ms;
}
.task-queue-item:hover { border-color: #BFDBFE; background: #FAFBFF; }
.task-queue-item.priority-urgent { border-left: 3px solid #EF4444; }
.task-queue-item.priority-high   { border-left: 3px solid #F59E0B; }
.task-queue-item.priority-normal { border-left: 3px solid #1D6BF3; }
.task-check { background: none; border: none; cursor: pointer; font-size: 18px; color: #D1D5DB; width: 28px; flex-shrink: 0; transition: color 120ms; }
.task-check:hover { color: #1D6BF3; }
.task-queue-content { flex: 1; min-width: 0; }
.task-queue-title { font-size: 13px; font-weight: 500; color: #0F1117; }
.task-queue-meta { font-size: 11px; color: #9CA3AF; display: flex; gap: 6px; align-items: center; margin-top: 2px; }
.task-due.overdue { color: #EF4444; font-weight: 600; }
.task-lead { color: #1D6BF3; }


=== 2. PROPOSALS: TIMELINE + HOT INDICATOR ===

File: app/templates/proposals/index.html

Replace plain table rows with STATUS CARDS:

<div class="proposal-cards-grid">
  {% for proposal in proposals %}
  <div class="proposal-card {% if proposal.is_hot %}proposal-hot{% endif %}">
    {% if proposal.is_hot %}
    <div class="proposal-hot-banner">
      <span class="proposal-hot-pulse"></span>
      Avattu äskettäin — ota yhteyttä nyt!
    </div>
    {% endif %}

    <div class="proposal-card-header">
      <div>
        <div class="proposal-ref">{{ proposal.reference_number }}</div>
        <div class="proposal-company">{{ proposal.lead.company }}</div>
      </div>
      <div class="proposal-value">€{{ '{:,.0f}'.format(proposal.total).replace(',', ' ') }}</div>
    </div>

    <!-- STATUS TIMELINE -->
    <div class="proposal-timeline">
      {% set steps = [('draft','Luotu'),('sent','Lähetetty'),('viewed','Avattu'),('accepted','Hyväksytty')] %}
      {% for step_key, step_label in steps %}
      <div class="proposal-timeline-step
        {% if proposal.status == step_key %}active
        {% elif proposal.status_rank > loop.index0 %}done
        {% endif %}">
        <div class="proposal-timeline-dot"></div>
        <div class="proposal-timeline-label">{{ step_label }}</div>
      </div>
      {% if not loop.last %}<div class="proposal-timeline-line {% if proposal.status_rank > loop.index0 %}done{% endif %}"></div>{% endif %}
      {% endfor %}
    </div>

    {% if proposal.view_count > 0 %}
    <div class="proposal-view-signal">
      👁 Avattu <strong>{{ proposal.view_count }}x</strong>
      {% if proposal.viewed_at %} · viimeksi {{ proposal.viewed_at | timeago }}{% endif %}
      {% if proposal.view_count >= 3 %}<span class="badge badge-warning" style="margin-left:6px;">Korkea kiinnostus</span>{% endif %}
    </div>
    {% endif %}

    <div class="proposal-card-footer">
      <span class="badge {{ proposal.status_badge_class }}">{{ proposal.status_label }}</span>
      <div style="display:flex;gap:8px;">
        {% if proposal.status == 'draft' %}
        <a href="/proposals/{{ proposal.id }}" class="btn btn-secondary btn-sm">Muokkaa</a>
        <button onclick="sendProposal({{ proposal.id }})" class="btn btn-primary btn-sm">Lähetä →</button>
        {% elif proposal.status == 'sent' or proposal.status == 'viewed' %}
        <a href="/leads/{{ proposal.lead.id }}" class="btn btn-secondary btn-sm">Avaa liidi</a>
        {% endif %}
      </div>
    </div>
  </div>
  {% endfor %}
</div>

CSS:
.proposal-cards-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.proposal-card {
  background: white; border-radius: 12px;
  border: 1px solid #E4E7EF;
  overflow: hidden;
  transition: box-shadow 150ms;
}
.proposal-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,0.08); }
.proposal-card.proposal-hot { border-color: #FCA5A5; }

.proposal-hot-banner {
  background: linear-gradient(90deg, #EF4444, #F97316);
  color: white; font-size: 12px; font-weight: 700;
  padding: 8px 16px;
  display: flex; align-items: center; gap: 8px;
}
.proposal-hot-pulse {
  width: 8px; height: 8px; border-radius: 50%; background: white;
  animation: pulse-ring 1.5s ease infinite;
}

.proposal-card-header { padding: 16px 16px 12px; display: flex; justify-content: space-between; align-items: flex-start; }
.proposal-ref { font-size: 11px; color: #9CA3AF; font-weight: 600; }
.proposal-company { font-size: 15px; font-weight: 700; color: #0F1117; }
.proposal-value { font-size: 18px; font-weight: 800; color: #1D6BF3; }

.proposal-timeline { padding: 0 16px 12px; display: flex; align-items: center; }
.proposal-timeline-step { display: flex; flex-direction: column; align-items: center; flex-shrink: 0; }
.proposal-timeline-dot { width: 10px; height: 10px; border-radius: 50%; background: #E5E7EB; border: 2px solid #E5E7EB; }
.proposal-timeline-step.done .proposal-timeline-dot { background: #10B981; border-color: #10B981; }
.proposal-timeline-step.active .proposal-timeline-dot { background: #1D6BF3; border-color: #1D6BF3; box-shadow: 0 0 0 3px rgba(29,107,243,0.2); }
.proposal-timeline-label { font-size: 9px; color: #9CA3AF; margin-top: 3px; white-space: nowrap; font-weight: 500; }
.proposal-timeline-step.done .proposal-timeline-label { color: #10B981; }
.proposal-timeline-step.active .proposal-timeline-label { color: #1D6BF3; font-weight: 700; }
.proposal-timeline-line { flex: 1; height: 2px; background: #E5E7EB; }
.proposal-timeline-line.done { background: #10B981; }

.proposal-view-signal { padding: 8px 16px; background: #FFFBEB; border-top: 1px solid #FDE68A; font-size: 12px; color: #92400E; }
.proposal-card-footer { padding: 12px 16px; border-top: 1px solid #F0F2F8; display: flex; justify-content: space-between; align-items: center; }

Backend: add to Proposal model:
  @property
  def is_hot(self): return self.viewed_at and (datetime.utcnow() - self.viewed_at).hours < 24
  @property
  def view_count(self): return self.views if hasattr(self, 'views') else (1 if self.viewed_at else 0)
  @property
  def status_rank(self): return {'draft':0,'sent':1,'viewed':2,'accepted':3,'declined':3}.get(self.status,0)


=== 3. SEQUENCES: VISUAL FLOW ===

File: app/templates/sequences/index.html

For each sequence, replace plain table row with FLOW PREVIEW:

<div class="sequence-card">
  <div class="sequence-card-header">
    <div>
      <div class="sequence-name">{{ sequence.name }}</div>
      <div class="sequence-meta">{{ sequence.steps|length }} askelta · {{ sequence.active_enrollments }} aktiivista</div>
    </div>
    <div style="display:flex;gap:8px;align-items:center;">
      <label class="toggle-switch">
        <input type="checkbox" {% if sequence.is_active %}checked{% endif %} onchange="toggleSequence({{ sequence.id }}, this.checked)">
        <span class="toggle-slider"></span>
      </label>
      <a href="/sequences/{{ sequence.id }}" class="btn btn-secondary btn-sm">Muokkaa</a>
    </div>
  </div>

  <!-- VISUAL STEP FLOW -->
  <div class="sequence-flow">
    <div class="sequence-trigger">
      <span class="seq-trigger-icon">⚡</span>
      <span class="seq-trigger-label">{{ sequence.trigger_label }}</span>
    </div>
    {% for step in sequence.steps %}
    <div class="seq-arrow">→</div>
    <div class="sequence-step-pill {% if step.open_rate and step.open_rate > 50 %}step-hot{% elif step.open_rate and step.open_rate < 20 %}step-cold{% endif %}"
         title="Avausprosentti: {{ step.open_rate or 0 }}%">
      <div class="step-pill-delay">+{{ step.delay_days }}pv</div>
      <div class="step-pill-subject">{{ step.subject_template[:25] }}{% if step.subject_template|length > 25 %}...{% endif %}</div>
      {% if step.open_rate is not none %}
      <div class="step-pill-rate">{{ step.open_rate }}% auki</div>
      {% endif %}
    </div>
    {% endfor %}
  </div>

  <!-- PERFORMANCE BAR -->
  {% if sequence.stats %}
  <div class="sequence-stats-row">
    <div class="seq-stat"><span class="seq-stat-num">{{ sequence.stats.enrolled }}</span><span class="seq-stat-label">Ilmoittautuneita</span></div>
    <div class="seq-stat"><span class="seq-stat-num">{{ sequence.stats.open_rate }}%</span><span class="seq-stat-label">Avausprosentti</span></div>
    <div class="seq-stat"><span class="seq-stat-num">{{ sequence.stats.reply_rate }}%</span><span class="seq-stat-label">Vastausprosentti</span></div>
    <div class="seq-stat"><span class="seq-stat-num text-success">{{ sequence.stats.completed }}</span><span class="seq-stat-label">Valmistunut</span></div>
  </div>
  {% endif %}
</div>

CSS:
.sequence-card { background: white; border: 1px solid #E4E7EF; border-radius: 12px; overflow: hidden; margin-bottom: 12px; }
.sequence-card-header { padding: 16px; display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 1px solid #F0F2F8; }
.sequence-name { font-size: 14px; font-weight: 700; color: #0F1117; }
.sequence-meta { font-size: 12px; color: #9CA3AF; margin-top: 2px; }

.sequence-flow { padding: 14px 16px; display: flex; align-items: center; gap: 6px; overflow-x: auto; }
.sequence-trigger { display: flex; align-items: center; gap: 6px; background: #EBF2FF; border: 1px solid #BFDBFE; border-radius: 20px; padding: 5px 12px; font-size: 11px; font-weight: 600; color: #1D6BF3; flex-shrink: 0; }
.seq-trigger-icon { font-size: 12px; }
.seq-arrow { color: #D1D5DB; font-size: 16px; flex-shrink: 0; }
.sequence-step-pill {
  background: #F4F6FB; border: 1px solid #E4E7EF;
  border-radius: 8px; padding: 7px 10px;
  font-size: 11px; text-align: center;
  min-width: 90px; flex-shrink: 0;
  cursor: pointer; transition: all 120ms;
}
.sequence-step-pill:hover { border-color: #1D6BF3; background: #EBF2FF; }
.sequence-step-pill.step-hot { border-color: #6EE7B7; background: #F0FDF4; }
.sequence-step-pill.step-cold { border-color: #FCA5A5; background: #FFF8F8; }
.step-pill-delay { font-size: 10px; color: #9CA3AF; font-weight: 600; }
.step-pill-subject { font-size: 11px; color: #374151; font-weight: 500; margin: 2px 0; }
.step-pill-rate { font-size: 10px; color: #10B981; font-weight: 700; }
.sequence-step-pill.step-cold .step-pill-rate { color: #EF4444; }

.sequence-stats-row { padding: 12px 16px; background: #F9FAFB; border-top: 1px solid #F0F2F8; display: flex; gap: 0; }
.seq-stat { flex: 1; text-align: center; border-right: 1px solid #E4E7EF; }
.seq-stat:last-child { border-right: none; }
.seq-stat-num { display: block; font-size: 16px; font-weight: 800; color: #0F1117; }
.seq-stat-label { font-size: 10px; color: #9CA3AF; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }

/* Toggle switch */
.toggle-switch { position:relative;display:inline-block;width:38px;height:20px; }
.toggle-switch input { display:none; }
.toggle-slider { position:absolute;cursor:pointer;inset:0;background:#D1D5DB;border-radius:20px;transition:200ms; }
.toggle-slider::before { content:'';position:absolute;width:16px;height:16px;left:2px;bottom:2px;background:white;border-radius:50%;transition:200ms; }
.toggle-switch input:checked + .toggle-slider { background:#1D6BF3; }
.toggle-switch input:checked + .toggle-slider::before { transform:translateX(18px); }
```

### ✅ Hyväksymiskriteerit
- [ ] Tehtävät: Focus card näyttää kiireellisimmän tehtävän isolla
- [ ] Complete toimii AJAXilla, tehtävä katoaa animaatiolla
- [ ] Tarjoukset: timeline näyttää vaiheet (Luotu → Lähetetty → Avattu → Hyväksytty)
- [ ] "Kuuma tarjous" (avattu 24h sisällä) näyttää pulssi-bannerin
- [ ] Sekvenssit: visuaalinen flow pillereillä ja nuolilla
- [ ] Sekvenssin step-pillerit näyttävät avausprosentit värikoodattuna

---

## VAIHE UX-5 — Density, Identity & Smart Empty States
**Arvio:** 1–2 päivää  
**Korjaa ongelmat:** #1 (tyhjä tila) · #2 (admin feel) · #16 (background)

### Cursor-prompt

```
Fix density, empty states, and visual identity across all FlowLeads CRM pages.
DO NOT change backend logic.

=== 1. SMART EMPTY STATES ===

Replace ALL "Ei X" texts with actionable, motivating empty states.
Create: app/templates/components/empty_state.html

{% macro empty_state(icon, title, description, primary_action_url, primary_action_label, secondary_actions=[]) %}
<div class="empty-state-card">
  <div class="empty-state-icon">{{ icon }}</div>
  <div class="empty-state-title">{{ title }}</div>
  <div class="empty-state-desc">{{ description }}</div>
  <div class="empty-state-actions">
    <a href="{{ primary_action_url }}" class="btn btn-primary">{{ primary_action_label }}</a>
    {% for action in secondary_actions %}
    <a href="{{ action.url }}" class="btn btn-secondary">{{ action.label }}</a>
    {% endfor %}
  </div>
  {% if caller is defined %}{{ caller() }}{% endif %}
</div>
{% endmacro %}

CSS:
.empty-state-card {
  text-align: center;
  padding: 48px 32px;
  background: white;
  border-radius: 14px;
  border: 1px solid #E4E7EF;
}
.empty-state-icon { font-size: 44px; margin-bottom: 14px; opacity: 0.8; }
.empty-state-title { font-size: 17px; font-weight: 700; color: #0F1117; margin-bottom: 8px; }
.empty-state-desc { font-size: 13px; color: #6B7280; max-width: 380px; margin: 0 auto 20px; line-height: 1.6; }
.empty-state-actions { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }

REPLACE EMPTY STATES PER PAGE:

Pipeline (no leads):
  icon: "🚀", title: "Lisää ensimmäinen liidi"
  desc: "Aloita myyntiputki nopeasti: lisää liidi, importtaa CSV tai yhdistä n8n ja seuraa etenemistä vaiheittain."
  primary: "/leads/new" → "+ Lisää liidi"
  secondary: ["/leads/import" → "Importtaa CSV", "/settings/api-keys" → "Yhdistä n8n"]
  Also show quick-tips below: "Nopeat tavat aloittaa: ..."

Leads (no leads):
  icon: "🎯"
  title: "Myyntityölistasi odottaa"
  desc: "Tämä on myyntityötilasi. Lisää ensimmäinen kontakti tai tuo liidejä automaattisesti integraatioista."
  primary: "/leads/new" → "Lisää ensimmäinen liidi"
  secondary: ["/settings/api-keys" → "Tuo n8n-workflowsta", "/forms/new" → "Luo verkkolomake"]

Tasks (no tasks today):
  icon: "🎉" → shows celebration, not emptiness
  title: "Ei tehtäviä tänään — olet ajan tasalla!"
  desc: "Olet suoriutunut kaikista tehtävistä. Haluatko katsoa liidit jotka tarvitsevat huomiota?"
  primary: "/leads?filter=no_contact_7d" → "Katso huomiota tarvitsevat liidit"

Proposals (no proposals):
  icon: "📄"
  title: "Luo ensimmäinen tarjouksesi"
  desc: "Lähetä siistejä tarjouksia suoraan CRM:stä. Näet milloin asiakas avaa tarjouksen."
  primary: "/leads" → "Valitse liidi ja luo tarjous"

Sequences (no sequences):
  icon: "⚡"
  title: "Automatisoi liidien nurturointi"
  desc: "Luo sähköpostisekvenssi joka hoitaa follow-upit puolestasi — automaattisesti, oikeaan aikaan."
  primary: "/sequences/new" → "Luo ensimmäinen sekvenssi"

Calendar (no events):
  icon: "📅"
  title: "Aikatauluta tapaaminen"
  desc: "Ei tapaamisia tänään. Kalenterisi on vapaa — hyvä hetki ottaa yhteyttä kuumaan liidiin."
  primary: "/pipeline" → "Katso kuumat liidit"


=== 2. COMPACT DENSITY — reduce padding when little data ===

Add to main.css:

/* Compact mode: triggered when page has few items */
.page-compact .card { margin-bottom: 12px; }
.page-compact .card-body { padding: 14px 16px; }
.page-compact .card-header { padding: 12px 16px; }
.page-compact .metric-card { padding: 14px; min-height: 90px; }
.page-compact .metric-value { font-size: 26px; }

/* Auto-compact: when table has < 5 rows */
.table-compact td { padding: 9px 12px; }
.table-compact th { padding: 8px 12px; }

/* Remove min-height from empty containers */
.pipeline-cards { min-height: 40px !important; }

JavaScript — add to base.html:
document.addEventListener('DOMContentLoaded', () => {
  const rows = document.querySelectorAll('tbody tr').length;
  if (rows < 6) document.querySelector('.page-content')?.classList.add('page-compact');
});


=== 3. BACKGROUND: SIGNATURE FLOWLY GRID PATTERN ===

Add to design-system.css:

.app-main {
  background-color: #F4F6FB;
  background-image:
    linear-gradient(rgba(29,107,243,0.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(29,107,243,0.025) 1px, transparent 1px);
  background-size: 40px 40px;
}

/* Subtle blue glow on page-content top edge */
.page-content::before {
  content: '';
  display: block;
  height: 2px;
  background: linear-gradient(90deg, transparent, rgba(29,107,243,0.15), transparent);
  margin-bottom: 20px;
  border-radius: 2px;
}


=== 4. HOVER STATES — make tables feel alive ===

Update all table rows:

tbody tr {
  border-bottom: 1px solid #F0F2F8;
  transition: background 100ms;
  cursor: pointer;
}
tbody tr:hover { background: #F4F7FF; }

/* Hover reveals action buttons that are hidden by default */
.row-actions { opacity: 0; transition: opacity 120ms; display: flex; gap: 6px; }
tbody tr:hover .row-actions { opacity: 1; }

.row-action-btn {
  padding: 3px 8px;
  font-size: 11px;
  border-radius: 5px;
  border: 1px solid #E4E7EF;
  background: white;
  color: #6B7280;
  cursor: pointer;
  text-decoration: none;
  transition: all 120ms;
  white-space: nowrap;
}
.row-action-btn:hover { background: #1D6BF3; color: white; border-color: #1D6BF3; }
.row-action-btn.primary { background: #1D6BF3; color: white; border-color: #1D6BF3; }


=== 5. NUMBERS BIGGER THAN LABELS — data-first hierarchy ===

Update metric cards and stat sections:
/* The number IS the message — make it huge */
.stat-primary-number { font-size: 40px; font-weight: 800; line-height: 1; letter-spacing: -1.5px; }
.stat-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-muted); margin-bottom: 8px; }

/* Destructive actions hidden by default */
.action-destructive { display: none; }
tr:hover .action-destructive,
.card:hover .action-destructive { display: inline-flex; }
```

### ✅ Hyväksymiskriteerit
- [ ] Jokainen tyhjä tila on motivoiva ja ohjaa toimintaan (ei pelkkä "Ei X")
- [ ] Pipeline tyhjä tila näyttää 3 tapaa aloittaa (lisää, importtaa, n8n)
- [ ] Tehtävät-tyhjä tila on positiivinen (ei "virhe"-tunnelma)
- [ ] Grid-pattern näkyy hienovaraisesti sisältöalueen taustassa
- [ ] Taulukkorivit elävöityvät hoverissa (taustaväri + action-napit)
- [ ] Destructive-napit (poista, arkistoi) näkyvät vain hoverissa
- [ ] Compact mode aktivoituu automaattisesti kun rivejä < 6

---

## Yhteenveto kaikki UX-vaiheet

| Vaihe | Teema | Päävaikutus | Arvio |
|---|---|---|---|
| UX-1 | Command Center Dashboard | Alert strip, AI Pulse, activity stream, workflow bar | 2–3 pv |
| UX-2 | AI-Native Identity | FAB-drawer, action badges, score-bar | 2 pv |
| UX-3 | Smart Pipeline & Leads | Engagement-kortit, preview drawer, Cmd+K | 2 pv |
| UX-4 | Execution Workflow Pages | Focus mode, proposal timeline, sequence flow | 2 pv |
| UX-5 | Density & Identity | Smart empty states, grid pattern, hover | 1–2 pv |
| **Yht.** | | **FlowLeads tuntuu oikealta AI CRM:ltä** | **~10 pv** |
