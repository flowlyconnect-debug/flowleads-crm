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
        var item = btn.closest('.priority-action-item');
        if (!item) return;

        item.classList.add('completing');
        setTimeout(function () {
          item.remove();
        }, 400);

        var badge = document.getElementById('todayTaskCount');
        if (badge) {
          badge.textContent = Math.max(0, parseInt(badge.textContent, 10) - 1);
        }
      })
      .catch(function () {});
  };

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
    refreshActivityStream();
    setInterval(refreshActivityStream, 30000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
