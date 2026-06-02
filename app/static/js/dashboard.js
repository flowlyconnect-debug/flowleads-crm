/**
 * FlowLeads CRM — Sales Command Center
 */
(function () {
  'use strict';

  var ACTIVITY_ICONS = {
    email_sent: '✉',
    stage_changed: '↗',
    created: '★',
    lead_created: '★',
    ai_enriched: '🤖',
    task_completed: '✓',
    call: '📞',
    proposal_viewed: '👁',
    proposal_sent: '📄',
  };

  function getCsrfToken() {
    return (
      document.querySelector('meta[name=csrf-token]')?.content ||
      document.getElementById('csrf-token')?.value ||
      ''
    );
  }

  function setCurrentDate() {
    var el = document.getElementById('dashboard-current-date');
    if (!el) return;
    try {
      el.textContent = new Intl.DateTimeFormat('fi-FI', {
        weekday: 'long',
        day: 'numeric',
        month: 'long',
        year: 'numeric',
      }).format(new Date());
    } catch (e) {
      el.textContent = new Date().toLocaleDateString('fi-FI');
    }
  }

  function escapeHtml(text) {
    var d = document.createElement('div');
    d.textContent = text == null ? '' : String(text);
    return d.innerHTML;
  }

  function activityIcon(type) {
    return ACTIVITY_ICONS[type] || '•';
  }

  function renderActivityItem(activity) {
    var type = activity.type || 'note';
    var subject = activity.subject || activity.lead_name || activity.user_label || 'System';
    var description = activity.description || activity.content_preview || type;
    var timeAgo = activity.time_ago || '';

    return (
      '<div class="activity-stream-item">' +
      '<div class="activity-stream-icon ' + escapeHtml(type) + '">' +
      activityIcon(type) +
      '</div>' +
      '<div class="activity-stream-content">' +
      '<div class="activity-stream-text">' +
      '<strong>' + escapeHtml(subject) + '</strong> — ' + escapeHtml(description) +
      '</div>' +
      '<div class="activity-stream-meta">' + escapeHtml(timeAgo) + '</div>' +
      '</div>' +
      '</div>'
    );
  }

  function updateActivityStream(activities) {
    var container = document.getElementById('activityStream');
    if (!container || !Array.isArray(activities)) return;

    if (!activities.length) {
      container.innerHTML =
        '<div class="dashboard-empty dashboard-empty--compact">Ei aktiviteettia vielä.</div>';
      return;
    }

    container.innerHTML = activities.map(renderActivityItem).join('');
  }

  function refreshActivityStream() {
    var root = document.getElementById('dashboard-command-center');
    if (!root) return;

    var orgQuery = {};
    try {
      orgQuery = JSON.parse(root.getAttribute('data-org-query') || '{}');
    } catch (e) {
      orgQuery = {};
    }

    var params = new URLSearchParams(orgQuery);
    var url = '/api/dashboard/activity-stream' + (params.toString() ? '?' + params.toString() : '');

    fetch(url, { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('activity stream unavailable');
        return r.json();
      })
      .then(function (data) {
        if (data.success && data.data && data.data.activities) {
          updateActivityStream(data.data.activities);
        }
      })
      .catch(function () {
        /* API optional — initial SSR feed remains */
      });
  }

  var PIPELINE_COLORS = {
    'Uusi liidi': '#1D6BF3',
    'Kontaktoitu': '#38BDF8',
    'Kvalifioitu': '#10B981',
    'Tarjous lähetetty': '#F59E0B',
    'Voitettu': '#22C55E',
    'Hävitty': '#EF4444',
  };

  function destroyPipelineChart(canvas) {
    if (canvas && canvas._flChart) {
      canvas._flChart.destroy();
      canvas._flChart = null;
    }
  }

  function renderPipelinePlaceholder(canvasEl, emptyEl) {
    if (typeof Chart === 'undefined' || !canvasEl) return;
    destroyPipelineChart(canvasEl);

    var ctx = canvasEl.getContext('2d');
    var chart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: ['Ei liidejä'],
        datasets: [
          {
            data: [1],
            backgroundColor: ['#E5E7EB'],
            borderWidth: 0,
          },
        ],
      },
      options: {
        cutout: '65%',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: false,
          },
          tooltip: {
            enabled: false,
          },
        },
      },
    });
    canvasEl._flChart = chart;
    if (emptyEl) emptyEl.style.display = 'flex';
  }

  function renderPipelineDonut(data) {
    var root = document.getElementById('dashboard-command-center');
    if (!root) return;

    var canvasEl = document.getElementById('pipelineDonutChart');
    var legendEl = document.getElementById('pipelineDonutLegend');
    var emptyEl = document.getElementById('pipelineDonutEmpty');
    if (!canvasEl || !legendEl || !emptyEl) return;

    if (!data || !data.success || !data.data) {
      renderPipelinePlaceholder(canvasEl, emptyEl);
      legendEl.innerHTML = '';
      return;
    }

    var payload = data.data;
    var labels = Array.isArray(payload.labels) ? payload.labels : [];
    var counts = Array.isArray(payload.counts) ? payload.counts : [];
    var percentages = Array.isArray(payload.percentages) ? payload.percentages : [];
    var total = typeof payload.total === 'number' ? payload.total : 0;

    if (!labels.length || !total) {
      renderPipelinePlaceholder(canvasEl, emptyEl);
      legendEl.innerHTML = '';
      return;
    }

    if (emptyEl) emptyEl.style.display = 'none';

    var colors = labels.map(function (label) {
      return PIPELINE_COLORS[label] || '#9CA3AF';
    });

    destroyPipelineChart(canvasEl);
    if (typeof Chart === 'undefined') return;

    var ctx = canvasEl.getContext('2d');
    var chart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [
          {
            data: counts,
            backgroundColor: colors,
            borderWidth: 0,
          },
        ],
      },
      options: {
        cutout: '65%',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            display: false,
          },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var idx = ctx.dataIndex;
                var label = labels[idx] || '';
                var pct = percentages[idx] != null ? percentages[idx] : 0;
                var count = counts[idx] != null ? counts[idx] : 0;
                return label + ': ' + count + ' (' + pct + ' %)';
              },
            },
          },
        },
      },
    });
    canvasEl._flChart = chart;

    // Render custom HTML legend
    var legendHtml = '';
    for (var i = 0; i < labels.length; i++) {
      var lbl = labels[i];
      var pctVal = percentages[i] != null ? percentages[i] : 0;
      legendHtml +=
        '<div class="pipeline-legend-row">' +
        '<div class="pipeline-legend-left">' +
        '<span class="pipeline-legend-dot" style="background-color:' +
        (PIPELINE_COLORS[lbl] || '#9CA3AF') +
        '"></span>' +
        '<span class="pipeline-legend-label">' +
        escapeHtml(lbl) +
        '</span>' +
        '</div>' +
        '<span class="pipeline-legend-value">' +
        pctVal +
        ' %</span>' +
        '</div>';
    }
    legendEl.innerHTML = legendHtml;
  }

  function refreshPipelineDonut() {
    var root = document.getElementById('dashboard-command-center');
    if (!root) return;

    var orgQuery = {};
    try {
      orgQuery = JSON.parse(root.getAttribute('data-org-query') || '{}');
    } catch (e) {
      orgQuery = {};
    }

    var params = new URLSearchParams(orgQuery);
    var url =
      '/api/dashboard/pipeline-distribution' +
      (params.toString() ? '?' + params.toString() : '');

    fetch(url, { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('pipeline distribution unavailable');
        return r.json();
      })
      .then(function (data) {
        renderPipelineDonut(data);
      })
      .catch(function () {
        var canvasEl = document.getElementById('pipelineDonutChart');
        var emptyEl = document.getElementById('pipelineDonutEmpty');
        var legendEl = document.getElementById('pipelineDonutLegend');
        if (legendEl) legendEl.innerHTML = '';
        renderPipelinePlaceholder(canvasEl, emptyEl);
      });
  }

  window.closeAlertStrip = function () {
    var strip = document.getElementById('alertStrip');
    if (strip) strip.classList.add('is-hidden');
    try {
      sessionStorage.setItem('flowleads-alert-strip-closed', '1');
    } catch (e) {}
  };

  window.completeTask = function (taskId, btn) {
    var url =
      btn.getAttribute('data-complete-url') ||
      '/tasks/' + taskId + '/complete';

    fetch(url, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      credentials: 'same-origin',
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.data.success) return;

        btn.classList.add('done');
        var item =
          btn.closest('.priority-action-item') || btn.closest('.today-item');
        if (!item) return;

        item.classList.add('completing');
        setTimeout(function () {
          item.remove();
          refreshDashboardMetrics();
          refreshAiWorklist();
        }, 400);

        var badge = document.getElementById('todayTaskCount');
        if (badge) {
          badge.textContent = Math.max(0, parseInt(badge.textContent, 10) - 1);
        }
      })
      .catch(function () {});
  };

  function getOrgQueryParams() {
    var root = document.getElementById('dashboard-command-center');
    if (!root) return '';
    var orgQuery = {};
    try {
      orgQuery = JSON.parse(root.getAttribute('data-org-query') || '{}');
    } catch (e) {
      orgQuery = {};
    }
    var params = new URLSearchParams(orgQuery);
    return params.toString() ? '?' + params.toString() : '';
  }

  function fetchDashboardJson(path) {
    return fetch(path + getOrgQueryParams(), { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error('request failed');
      return r.json();
    });
  }

  function renderHotLeads(leads) {
    var body = document.querySelector('#todayHotLeads .today-card-body');
    if (!body) return;
    if (!leads || !leads.length) {
      body.innerHTML = '<div class="today-empty">Ei kuumia liidejä juuri nyt.</div>';
      return;
    }
    body.innerHTML = leads
      .map(function (lead) {
        var badgeClass = lead.score_tier === 'hot' ? 'score-badge-hot' : 'score-badge-warm';
        var contactMeta =
          lead.days_since_contact != null
            ? 'Viimeisin kontakti ' + lead.days_since_contact + ' pv sitten'
            : 'Ei kontaktia vielä';
        return (
          '<div class="today-item">' +
          '<div class="today-item-head">' +
          '<div><div class="today-item-name">' +
          escapeHtml(lead.name) +
          '</div>' +
          (lead.company
            ? '<div class="today-item-company">' + escapeHtml(lead.company) + '</div>'
            : '') +
          '</div>' +
          '<span class="score-badge ' +
          badgeClass +
          '">' +
          escapeHtml(lead.score_label || '') +
          '</span></div>' +
          '<div class="today-item-meta">' +
          escapeHtml(contactMeta) +
          '</div>' +
          '<div class="today-item-actions">' +
          '<a class="btn-today" href="' +
          escapeHtml(lead.url) +
          '">Avaa</a></div></div>'
        );
      })
      .join('');
  }

  function renderOverdueTasks(tasks) {
    var body = document.querySelector('#todayOverdueTasks .today-card-body');
    if (!body) return;
    if (!tasks || !tasks.length) {
      body.innerHTML = '<div class="today-empty">Ei myöhässä olevia tehtäviä.</div>';
      return;
    }
    body.innerHTML = tasks
      .map(function (task) {
        var leadLink = task.lead_url
          ? '<a class="btn-today" href="' +
            escapeHtml(task.lead_url) +
            '">Avaa liidi</a>'
          : '';
        return (
          '<div class="today-item" data-task-id="' +
          escapeHtml(task.id) +
          '">' +
          '<div class="today-item-name">' +
          escapeHtml(task.lead_name) +
          '</div>' +
          '<div class="today-item-company">' +
          escapeHtml(task.title) +
          '</div>' +
          '<div class="today-item-meta text-danger">' +
          escapeHtml(task.days_overdue) +
          ' pv myöhässä</div>' +
          '<div class="today-item-actions">' +
          '<button type="button" class="btn-today btn-today-complete" ' +
          'data-complete-url="' +
          escapeHtml(task.complete_url) +
          '" onclick="completeTask(' +
          task.id +
          ', this)">Merkitse tehdyksi</button>' +
          leadLink +
          '</div></div>'
        );
      })
      .join('');
  }

  function renderUnprocessedLeads(leads) {
    var body = document.querySelector('#todayUnprocessed .today-card-body');
    if (!body) return;
    if (!leads || !leads.length) {
      body.innerHTML = '<div class="today-empty">Ei uusia käsittelemättömiä liidejä.</div>';
      return;
    }
    body.innerHTML = leads
      .map(function (lead) {
        return (
          '<div class="today-item">' +
          '<div class="today-item-name">' +
          escapeHtml(lead.name) +
          '</div>' +
          (lead.company
            ? '<div class="today-item-company">' + escapeHtml(lead.company) + '</div>'
            : '') +
          '<div class="today-item-meta">' +
          escapeHtml(lead.created_at_relative) +
          (lead.source ? ' · ' + escapeHtml(lead.source) : '') +
          '</div>' +
          '<div class="today-item-actions">' +
          '<a class="btn-today" href="' +
          escapeHtml(lead.url) +
          '">Aloita</a></div></div>'
        );
      })
      .join('');
  }

  function renderAiRecommendations(recs) {
    var body = document.querySelector('#todayAiRecs .today-card-body');
    if (!body) return;
    if (!recs || !recs.length) {
      body.innerHTML = '<div class="today-empty">Ei AI-suosituksia tällä hetkellä.</div>';
      return;
    }
    body.innerHTML = recs
      .map(function (rec) {
        var badgeClass =
          rec.type === 'risk' ? 'rec-badge-risk' : 'rec-badge-opportunity';
        return (
          '<div class="today-item">' +
          '<div class="today-item-head">' +
          '<div><div class="today-item-name">' +
          escapeHtml(rec.name) +
          '</div>' +
          (rec.company
            ? '<div class="today-item-company">' + escapeHtml(rec.company) + '</div>'
            : '') +
          '</div>' +
          '<span class="rec-badge ' +
          badgeClass +
          '">' +
          (rec.type === 'risk' ? 'Riski' : 'Mahdollisuus') +
          '</span></div>' +
          '<div class="today-item-rec">' +
          escapeHtml(rec.recommendation) +
          '</div>' +
          '<div class="today-item-actions">' +
          '<a class="btn-today" href="' +
          escapeHtml(rec.url) +
          '">Avaa</a></div></div>'
        );
      })
      .join('');
  }

  function renderAiWorklist(items) {
    var body = document.getElementById('aiWorklistBody');
    if (!body) return;
    if (!items || !items.length) {
      body.innerHTML =
        '<div class="ai-worklist-empty"><span class="ai-worklist-empty-icon">✓</span>Ei kiireellisiä toimia tänään — hyvää työtä!</div>';
      return;
    }
    body.innerHTML = items
      .map(function (item, index) {
        return (
          '<div class="ai-worklist-item">' +
          '<span class="ai-worklist-rank">' + (index + 1) + '</span>' +
          '<div class="ai-worklist-content">' +
          '<div class="ai-worklist-text">' + escapeHtml(item.action_text || '') + '</div>' +
          '<div class="ai-worklist-reason">' + escapeHtml(item.reason || '') + '</div>' +
          '</div>' +
          '<a class="btn-today" href="' + escapeHtml(item.url) + '">Avaa →</a></div>'
        );
      })
      .join('');
  }

  function renderDashboardMetrics(metrics) {
    if (!metrics) return;
    var newLeads = document.getElementById('metricNewLeads');
    var trend = document.getElementById('metricNewLeadsTrend');
    var hotLeads = document.getElementById('metricHotLeads');
    var tasksToday = document.getElementById('metricTasksToday');
    var overdue = document.getElementById('metricTasksOverdue');
    var pipeline = document.getElementById('metricPipelineValue');

    if (newLeads) newLeads.textContent = metrics.new_leads_7d || 0;
    if (trend) {
      var delta = Number(metrics.new_leads_delta_pct || 0);
      var sign = delta >= 0 ? '+' : '-';
      trend.textContent = sign + Math.abs(delta) + '% vs edellinen vko';
    }
    if (hotLeads) hotLeads.textContent = metrics.hot_leads || 0;
    if (tasksToday) tasksToday.textContent = metrics.tasks_today || 0;
    if (overdue) {
      var overdueCount = metrics.overdue_tasks || 0;
      overdue.textContent = overdueCount > 0 ? overdueCount + ' myöhässä' : '';
    }
    if (pipeline) {
      if (metrics.pipeline_value == null || Number(metrics.pipeline_value) <= 0) {
        pipeline.textContent = 'Ei dataa';
      } else {
        pipeline.textContent =
          '€' + new Intl.NumberFormat('fi-FI', { maximumFractionDigits: 0 }).format(metrics.pipeline_value);
      }
    }
  }

  function refreshDashboardMetrics() {
    fetchDashboardJson('/api/dashboard/metrics')
      .then(function (data) {
        if (!data.success || !data.data) return;
        renderDashboardMetrics(data.data);
      })
      .catch(function () {
        /* metrics fallback keeps SSR defaults */
      });
  }

  function refreshAiWorklist() {
    fetchDashboardJson('/api/dashboard/ai-worklist')
      .then(function (data) {
        if (!data.success || !data.data) return;
        renderAiWorklist(data.data.items);
      })
      .catch(function () {
        var body = document.getElementById('aiWorklistBody');
        if (body) body.innerHTML = '<div class="today-empty">Lataus epäonnistui.</div>';
      });
  }

  function initAlertStrip() {
    var strip = document.getElementById('alertStrip');
    if (!strip) return;
    try {
      if (sessionStorage.getItem('flowleads-alert-strip-closed') === '1') {
        strip.classList.add('is-hidden');
      }
    } catch (e) {}
  }

  function init() {
    setCurrentDate();
    initAlertStrip();
    refreshDashboardMetrics();
    refreshAiWorklist();
    refreshActivityStream();
    setInterval(refreshActivityStream, 30000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
