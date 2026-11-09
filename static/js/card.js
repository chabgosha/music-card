/* Страница получателя: конверт → открытка со звуком. Если открытка отложена — обратный отсчёт. */
(function () {
  "use strict";

  var envelope = document.getElementById("envelope");
  if (!envelope) return;

  // ----- отложенная открытка -----
  if (envelope.dataset.locked === "true") {
    var openAt = new Date(envelope.dataset.openAt);
    document.getElementById("open-date").textContent = openAt.toLocaleString("ru-RU", {
      day: "numeric", month: "long", hour: "2-digit", minute: "2-digit"
    });
    var cd = document.getElementById("countdown");
    var tick = function () {
      var left = Math.max(0, openAt - Date.now());
      if (left <= 0) { window.location.reload(); return; }
      var s = Math.floor(left / 1000);
      var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
      var pad = function (n) { return String(n).padStart(2, "0"); };
      cd.textContent = (d ? d + " д " : "") + pad(h) + ":" + pad(m) + ":" + pad(sec);
    };
    tick();
    setInterval(tick, 1000);
    return;
  }

  // ----- открыть конверт -----
  var btn = document.getElementById("open-btn");
  var opened = document.getElementById("opened");
  var audio = document.getElementById("card-audio");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  btn.addEventListener("click", function () {
    // звук запускаем сразу по нажатию — так браузеры разрешают автовоспроизведение
    audio.play().catch(function () {});
    envelope.classList.add("is-leaving");
    setTimeout(function () {
      envelope.hidden = true;
      opened.hidden = false;
      if (!reduce) opened.classList.add("is-arriving");
      opened.scrollIntoView({ block: "start" });
    }, reduce ? 0 : 380);
  });
})();
