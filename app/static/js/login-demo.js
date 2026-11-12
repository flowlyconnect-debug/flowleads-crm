// Kirjautumissivun "Kohteesta kauppaan" -demo: vaiheet välilehtinä, valitut kohteet
// kulkevat liideiksi, soittolistalle ja tarjoukseen. Kaikki data on sivulla valmiina;
// ilman JavaScriptiä kaikki vaiheet näkyvät allekkain.
(function () {
  var root = document.querySelector("[data-lp-flow]");
  if (!root) return;
  root.setAttribute("data-ready", "");

  var tabs = root.querySelectorAll("[data-lp-flow-tab]");
  var panels = root.querySelectorAll("[data-lp-flow-panel]");
  var picks = root.querySelectorAll("[data-lp-flow-pick]");
  var goButtons = root.querySelectorAll("[data-lp-flow-go]");

  function selectedIds() {
    var ids = [];
    picks.forEach(function (box) {
      if (box.checked) ids.push(box.value);
    });
    return ids;
  }

  function sync() {
    var ids = selectedIds();
    root.querySelectorAll("[data-lp-flow-count]").forEach(function (el) {
      el.textContent = String(ids.length);
    });
    root.querySelectorAll("[data-lp-flow-item]").forEach(function (el) {
      el.hidden = ids.indexOf(el.getAttribute("data-lp-flow-item")) === -1;
    });
    var first = root.querySelector("[data-lp-flow-pick]:checked");
    var name = first ? first.closest(".lp-target").querySelector(".lp-target__name").firstChild.textContent.trim() : "";
    root.querySelectorAll("[data-lp-flow-first]").forEach(function (el) {
      if (name) el.textContent = name;
    });
    var go = root.querySelector('[data-lp-flow-go="1"]');
    if (go) go.disabled = ids.length === 0;
  }

  function show(index, focusTab) {
    tabs.forEach(function (tab, i) {
      var on = i === index;
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
      if (i < index) tab.classList.add("is-done");
      else tab.classList.remove("is-done");
    });
    panels.forEach(function (panel, i) {
      panel.classList.toggle("is-active", i === index);
    });
    if (focusTab) tabs[index].focus();
  }

  function reset() {
    picks.forEach(function (box) {
      box.checked = box.defaultChecked;
    });
    root.querySelectorAll("[data-lp-call]").forEach(function (row) {
      row.classList.remove("is-done");
      row.querySelector("[data-lp-call-result]").textContent = "";
      row.querySelectorAll("[data-lp-call-outcome]").forEach(function (b) {
        b.setAttribute("aria-pressed", "false");
      });
    });
    var status = root.querySelector("[data-lp-offer-status]");
    if (status) {
      status.textContent = "Lähetetty";
      status.classList.remove("is-won");
    }
    sync();
  }

  tabs.forEach(function (tab, i) {
    tab.addEventListener("click", function () {
      show(i, false);
    });
    tab.addEventListener("keydown", function (e) {
      var next = null;
      if (e.key === "ArrowRight") next = (i + 1) % tabs.length;
      if (e.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
      if (e.key === "Home") next = 0;
      if (e.key === "End") next = tabs.length - 1;
      if (next !== null) {
        e.preventDefault();
        show(next, true);
      }
    });
  });

  goButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var target = parseInt(btn.getAttribute("data-lp-flow-go"), 10);
      if (target === 0) reset();
      show(target, true);
    });
  });

  picks.forEach(function (box) {
    box.addEventListener("change", sync);
  });

  root.querySelectorAll("[data-lp-call]").forEach(function (row) {
    var result = row.querySelector("[data-lp-call-result]");
    row.querySelectorAll("[data-lp-call-outcome]").forEach(function (btn) {
      btn.setAttribute("aria-pressed", "false");
      btn.addEventListener("click", function () {
        row.querySelectorAll("[data-lp-call-outcome]").forEach(function (b) {
          b.setAttribute("aria-pressed", b === btn ? "true" : "false");
        });
        row.classList.add("is-done");
        result.textContent = btn.getAttribute("data-lp-call-outcome") + ". Kirjattu liidille.";
      });
    });
  });

  var accept = root.querySelector("[data-lp-offer-accept]");
  if (accept) {
    accept.addEventListener("click", function () {
      var status = root.querySelector("[data-lp-offer-status]");
      status.textContent = "Hyväksytty · Voitettu";
      status.classList.add("is-won");
    });
  }

  sync();
})();
