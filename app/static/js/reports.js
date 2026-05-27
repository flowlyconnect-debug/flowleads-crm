/**
 * FlowLeads — analytics report charts (Chart.js)
 */
(function () {
  'use strict';

  var CHART_COLORS = [
    '#1D6BF3',
    '#38BDF8',
    '#10B981',
    '#F59E0B',
    '#EC4899',
    '#8B5CF6',
    '#6B7280',
  ];

  function readPayload() {
    var el = document.getElementById('report-chart-data');
    if (!el || !el.textContent) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return null;
    }
  }

  function destroyExisting(canvas) {
    if (canvas && canvas._flChart) {
      canvas._flChart.destroy();
      canvas._flChart = null;
    }
  }

  function baseOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          position: 'bottom',
          labels: {
            boxWidth: 10,
            padding: 12,
            font: { family: 'Inter, sans-serif', size: 11 },
            color: '#5C6170',
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { font: { size: 11 }, color: '#9CA3AF', maxRotation: 45 },
        },
        y: {
          beginAtZero: true,
          grid: { color: 'rgba(228, 231, 239, 0.6)' },
          ticks: { font: { size: 11 }, color: '#9CA3AF' },
        },
      },
    };
  }

  function renderChart(payload) {
    if (!payload || typeof Chart === 'undefined') return;
    var canvas = document.getElementById('report-chart') || document.getElementById('forecast-chart');
    if (!canvas) return;
    destroyExisting(canvas);

    var ctx = canvas.getContext('2d');
    var opts = baseOptions();
    var chart;

    if (payload.type === 'doughnut' || payload.type === 'pie') {
      chart = new Chart(ctx, {
        type: payload.type,
        data: {
          labels: payload.labels,
          datasets: [{
            data: payload.values,
            backgroundColor: CHART_COLORS.slice(0, payload.labels.length),
            borderWidth: 0,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: {
              position: 'bottom',
              labels: {
                boxWidth: 10,
                padding: 10,
                font: { family: 'Inter, sans-serif', size: 11 },
                color: '#5C6170',
              },
            },
          },
        },
      });
    } else {
      chart = new Chart(ctx, {
        type: payload.type || 'bar',
        data: {
          labels: payload.labels,
          datasets: [{
            label: payload.datasetLabel || '',
            data: payload.values,
            backgroundColor: CHART_COLORS[0],
            borderRadius: 6,
            maxBarThickness: 48,
          }],
        },
        options: opts,
      });
    }

    canvas._flChart = chart;
  }

  function init() {
    var payload = readPayload();
    if (payload) renderChart(payload);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
