/**
 * FlowLeads CRM — presentation-layer utilities (sidebar, layout)
 */
(function () {
  'use strict';

  function initSidebar() {
    const sidebar = document.getElementById('app-sidebar');
    const toggle = document.getElementById('sidebar-toggle');
    const backdrop = document.getElementById('sidebar-backdrop');
    if (!sidebar || !toggle) return;

    function setOpen(open) {
      sidebar.classList.toggle('is-open', open);
      if (backdrop) backdrop.classList.toggle('is-visible', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      document.body.classList.toggle('sidebar-open', open);
    }

    function close() {
      setOpen(false);
    }

    toggle.addEventListener('click', function () {
      setOpen(!sidebar.classList.contains('is-open'));
    });

    if (backdrop) {
      backdrop.addEventListener('click', close);
    }

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') close();
    });

    window.addEventListener('resize', function () {
      if (window.innerWidth > 1024) close();
    });

    sidebar.querySelectorAll('.sidebar-item').forEach(function (link) {
      link.addEventListener('click', function () {
        if (window.innerWidth <= 1024) close();
      });
    });
  }

  function initModals() {
    const backdrops = document.querySelectorAll('.modal-backdrop');
    if (!backdrops.length) return;

    function setBodyLock(locked) {
      document.body.classList.toggle('modal-open', locked);
    }

    function openModal(modal) {
      if (!modal) return;
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      setBodyLock(true);
    }

    function closeModal(modal) {
      if (!modal) return;
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      if (!document.querySelector('.modal-backdrop.open')) {
        setBodyLock(false);
      }
    }

    document.querySelectorAll('[data-open-modal]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        openModal(document.getElementById(btn.getAttribute('data-open-modal')));
      });
    });

    document.querySelectorAll('[data-close-modal]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        closeModal(document.getElementById(btn.getAttribute('data-close-modal')));
      });
    });

    backdrops.forEach(function (backdrop) {
      backdrop.setAttribute('aria-hidden', 'true');
      backdrop.addEventListener('click', function (e) {
        if (e.target === backdrop) closeModal(backdrop);
      });
    });

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      document.querySelectorAll('.modal-backdrop.open').forEach(closeModal);
    });
  }

  function initReportsFilters() {
    const rangeSelect = document.getElementById('range-select');
    const customRow = document.getElementById('reports-custom-dates');
    if (!rangeSelect || !customRow) return;
    function syncCustom() {
      customRow.classList.toggle('is-visible', rangeSelect.value === 'custom');
    }
    rangeSelect.addEventListener('change', syncCustom);
    syncCustom();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      initSidebar();
      initModals();
      initReportsFilters();
    });
  } else {
    initSidebar();
    initModals();
    initReportsFilters();
  }
})();


// Filter bars (Kohteet, Liidit): submit on change, without empty parameters in the URL.
window.flSubmitFilters = function (form) {
  Array.prototype.forEach.call(form.elements, function (field) {
    if (field.name && field.value === '' && field.type !== 'hidden') field.disabled = true;
  });
  form.submit();
};

// Instant feedback on every ordinary form post: the pressed button shows a
// spinner straight away and the form cannot be sent twice while the next page
// loads. Forms handled by their own script (they call preventDefault) and
// forms marked data-no-busy are left alone.
(function () {
  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented) return;
    if (form.hasAttribute('data-no-busy') || form.target === '_blank') return;
    if (form.dataset.busy === '1') {
      event.preventDefault();
      return;
    }
    form.dataset.busy = '1';
    var button = event.submitter;
    // disable only after the browser has read the pressed button's name/value
    window.setTimeout(function () {
      if (button && button.tagName === 'BUTTON') {
        button.classList.add('is-busy');
        button.setAttribute('aria-busy', 'true');
      }
      form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]').forEach(function (b) {
        if (b.disabled) return; // was already off (e.g. nothing selected): leave it so
        b.disabled = true;
        b.setAttribute('data-busy-disabled', '1');
      });
    }, 0);
    // a download or a validation error keeps the page: release the form again
    window.setTimeout(function () { release(form); }, 8000);
  });

  function release(form) {
    delete form.dataset.busy;
    form.querySelectorAll('[aria-busy="true"]').forEach(function (b) {
      b.classList.remove('is-busy');
      b.removeAttribute('aria-busy');
    });
    form.querySelectorAll('[data-busy-disabled]').forEach(function (b) {
      b.disabled = false;
      b.removeAttribute('data-busy-disabled');
    });
  }

  // coming back with the browser's back button shows the page from cache
  window.addEventListener('pageshow', function (event) {
    if (event.persisted) document.querySelectorAll('form[data-busy="1"]').forEach(release);
  });
})();
