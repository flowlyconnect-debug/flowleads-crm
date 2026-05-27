(function () {
  "use strict";

  var script = document.currentScript;
  if (!script) return;

  var token = script.getAttribute("data-form-token");
  var targetSel = script.getAttribute("data-target") || "#flowleads-form";
  if (!token) {
    console.error("FlowLeads embed: data-form-token is required");
    return;
  }

  var target = document.querySelector(targetSel);
  if (!target) {
    console.error("FlowLeads embed: target not found:", targetSel);
    return;
  }

  var base = script.src.replace(/\/static\/forms\/embed\.js.*$/, "");
  var apiBase = base + "/api/public/forms/" + encodeURIComponent(token);

  var styles = document.createElement("style");
  styles.textContent =
    ".fl-embed{font-family:system-ui,-apple-system,sans-serif;color:#0f172a;max-width:520px;margin:0 auto}" +
    ".fl-embed h2{font-size:1.25rem;margin:0 0 .5rem}" +
    ".fl-embed .fl-desc{color:#64748b;margin-bottom:1rem;font-size:.95rem}" +
    ".fl-field{margin-bottom:.9rem}" +
    ".fl-embed label{display:block;font-size:.875rem;font-weight:600;margin-bottom:.3rem}" +
    ".fl-req{color:#dc2626}" +
    ".fl-embed input,.fl-embed textarea,.fl-embed select{width:100%;padding:.5rem .6rem;border:1px solid #cbd5e1;border-radius:6px;font-size:1rem;box-sizing:border-box}" +
    ".fl-embed input:focus,.fl-embed textarea:focus,.fl-embed select:focus{outline:2px solid #38bdf8;border-color:#38bdf8}" +
    ".fl-checkbox{display:flex;align-items:center;gap:.5rem;font-weight:400}" +
    ".fl-checkbox input{width:auto}" +
    ".fl-error{display:block;color:#dc2626;font-size:.8rem;margin-top:.2rem;min-height:1rem}" +
    ".fl-global-error{background:#fef2f2;color:#b91c1c;padding:.65rem;border-radius:6px;margin-bottom:.75rem;display:none}" +
    ".fl-embed button[type=submit]{width:100%;padding:.65rem;background:#0ea5e9;color:#fff;border:none;border-radius:6px;font-size:1rem;font-weight:600;cursor:pointer;margin-top:.25rem}" +
    ".fl-embed button[type=submit]:disabled{opacity:.6;cursor:wait}" +
    ".fl-success{text-align:center;padding:1.5rem;color:#166534}" +
    ".fl-hp{position:absolute;left:-9999px;opacity:0;height:0;width:0;overflow:hidden}";
  document.head.appendChild(styles);

  target.innerHTML = '<div class="fl-embed"><p>Ladataan lomaketta…</p></div>';

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function fieldHtml(field) {
    var key = field.key;
    var label = field.label || key;
    var type = field.type || "text";
    var req = field.required;
    var reqAttr = req ? ' required aria-required="true"' : "";
    var reqMark = req ? ' <span class="fl-req">*</span>' : "";
    var id = "fl-e-" + key;
    var html = '<div class="fl-field" data-field="' + esc(key) + '">';

    if (type !== "checkbox") {
      html += '<label for="' + id + '">' + esc(label) + reqMark + "</label>";
    }

    if (type === "textarea") {
      html += '<textarea id="' + id + '" name="' + esc(key) + '" rows="4"' + reqAttr + "></textarea>";
    } else if (type === "select") {
      var opts = (field.options || [])
        .map(function (o) {
          return '<option value="' + esc(o) + '">' + esc(o) + "</option>";
        })
        .join("");
      html +=
        '<select id="' + id + '" name="' + esc(key) + '"' + reqAttr + '>' +
        '<option value="">—</option>' + opts + "</select>";
    } else if (type === "checkbox") {
      html +=
        '<label class="fl-checkbox"><input type="checkbox" id="' + id + '" name="' +
        esc(key) + '" value="1"> ' + esc(label) + reqMark + "</label>";
    } else {
      var inputType = ["email", "tel", "number"].indexOf(type) >= 0 ? type : "text";
      html +=
        '<input type="' + inputType + '" id="' + id + '" name="' + esc(key) + '"' + reqAttr + ">";
    }
    html += '<span class="fl-error" role="alert"></span></div>';
    return html;
  }

  function renderForm(def) {
    var fields = def.fields || [];
    var fieldsHtml = fields.map(fieldHtml).join("");
    target.innerHTML =
      '<div class="fl-embed" id="fl-embed-root">' +
      "<h2>" + esc(def.title) + "</h2>" +
      (def.description ? '<p class="fl-desc">' + esc(def.description) + "</p>" : "") +
      '<div class="fl-global-error" id="fl-global-err" role="alert"></div>' +
      '<form id="fl-embed-form" novalidate>' +
      fieldsHtml +
      '<div class="fl-hp" aria-hidden="true"><input type="text" name="_hp" tabindex="-1" autocomplete="off"></div>' +
      '<button type="submit">' + esc(def.submit_button_text || "Lähetä") + "</button>" +
      "</form></div>";

    var form = document.getElementById("fl-embed-form");
    var globalErr = document.getElementById("fl-global-err");
    var root = document.getElementById("fl-embed-root");

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      globalErr.style.display = "none";
      globalErr.textContent = "";
      form.querySelectorAll(".fl-error").forEach(function (el) {
        el.textContent = "";
      });
      var btn = form.querySelector('button[type="submit"]');
      btn.disabled = true;
      var body = {};
      new FormData(form).forEach(function (v, k) {
        body[k] = v;
      });
      fetch(apiBase + "/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (res) {
          return res.json().then(function (json) {
            return { res: res, json: json };
          });
        })
        .then(function (_ref) {
          var json = _ref.json;
          if (json.success) {
            root.innerHTML =
              '<div class="fl-success"><p>' + esc(json.message || def.success_message || "Kiitos!") + "</p></div>";
            return;
          }
          var err = json.error || {};
          if (err.fields) {
            Object.keys(err.fields).forEach(function (key) {
              var wrap = form.querySelector('[data-field="' + key + '"]');
              if (!wrap) return;
              var el = wrap.querySelector(".fl-error");
              if (el) el.textContent = err.fields[key];
            });
          }
          globalErr.textContent = err.message || "Lähetys epäonnistui.";
          globalErr.style.display = "block";
        })
        .catch(function () {
          globalErr.textContent = "Yhteysvirhe. Yritä uudelleen.";
          globalErr.style.display = "block";
        })
        .finally(function () {
          btn.disabled = false;
        });
    });
  }

  fetch(apiBase, { headers: { Accept: "application/json" } })
    .then(function (res) {
      return res.json();
    })
    .then(function (json) {
      if (!json.success || !json.data) {
        target.innerHTML = '<div class="fl-embed"><p>Lomaketta ei löydy.</p></div>';
        return;
      }
      renderForm(json.data);
    })
    .catch(function () {
      target.innerHTML = '<div class="fl-embed"><p>Lomakkeen lataus epäonnistui.</p></div>';
    });
})();
