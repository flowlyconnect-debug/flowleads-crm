// Kirjautumissivun karttaesimerkki: paikkakunnan valinta vaihtaa kohdelistan.
(function () {
  var root = document.querySelector("[data-lp-map]");
  if (!root) return;
  var pins = root.querySelectorAll("[data-lp-map-pin]");
  var panels = root.querySelectorAll("[data-lp-map-panel]");
  var timer = null;

  function select(index) {
    pins.forEach(function (pin, i) {
      pin.classList.toggle("is-active", i === index);
      pin.setAttribute("aria-pressed", i === index ? "true" : "false");
    });
    panels.forEach(function (panel, i) {
      panel.classList.toggle("is-active", i === index);
    });
  }

  function stop() {
    if (timer) window.clearInterval(timer);
    timer = null;
  }

  pins.forEach(function (pin, i) {
    pin.addEventListener("click", function () {
      stop();
      select(i);
    });
  });

  // Kierrätä paikkakuntia itsestään, kunnes käyttäjä valitsee yhden.
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!reduced && pins.length > 1) {
    var current = 0;
    timer = window.setInterval(function () {
      current = (current + 1) % pins.length;
      select(current);
    }, 3500);
    root.addEventListener("pointerenter", stop);
    root.addEventListener("focusin", stop);
  }
})();
