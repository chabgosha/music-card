/* Страница статуса для отправителя: когда открыли, сколько раз слушали, ответы-селфи.
   Обновляется сама каждые 5 секунд — новые улыбки появляются без перезагрузки. */
(function () {
  "use strict";

  var data = window.STATUS_DATA;
  var token = window.STATUS_TOKEN;
  var $ = function (sel) { return document.querySelector(sel); };
  var shown = {};
  var firstRender = true;
  var baseTitle = document.title;

  function fmt(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    var today = new Date();
    var time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === today.toDateString()) return "сегодня в " + time;
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" }) + " в " + time;
  }

  function times(n) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return n + " раз";
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return n + " раза";
    return n + " раз";
  }

  function setStep(el, done, text) {
    el.dataset.done = done ? "true" : "false";
    el.querySelector(".st-step__text").textContent = text;
  }

  function render(s) {
    setStep($("#st-created"), true, "Создана " + fmt(s.created_at));

    var locked = s.open_at && new Date(s.open_at) > new Date();
    if (s.first_seen_at) {
      var text = "Открыта " + fmt(s.first_seen_at);
      if (s.seen_count > 1) text += " · всего " + times(s.seen_count) + ", последний — " + fmt(s.last_seen_at);
      setStep($("#st-opened"), true, text);
    } else if (locked) {
      setStep($("#st-opened"), false, "Откроется " + fmt(s.open_at));
    } else {
      setStep($("#st-opened"), false, "Ещё не открыта");
    }

    setStep($("#st-listened"), s.listens > 0,
      s.listens > 0 ? "Прослушана " + times(s.listens) : "Ещё не слушали");

    var list = $("#replies");
    var fresh = 0;
    s.replies.slice().reverse().forEach(function (r) {
      if (shown[r.id]) return;
      shown[r.id] = true;
      fresh++;
      var fig = document.createElement("figure");
      fig.className = "polaroid polaroid--reply" + (firstRender ? "" : " is-new");
      var img = document.createElement("img");
      img.src = r.url;
      img.alt = "Ответ улыбкой";
      img.loading = "lazy";
      var cap = document.createElement("figcaption");
      cap.className = "polaroid__caption hand";
      cap.textContent = fmt(r.created_at);
      fig.appendChild(img);
      fig.appendChild(cap);
      list.insertBefore(fig, list.firstChild);
    });
    $("#replies-empty").hidden = s.replies.length > 0;
    $("#replies-count").textContent = s.replies.length ? "(" + s.replies.length + ")" : "";
    if (!firstRender && fresh) document.title = "📸 Новая улыбка! · " + baseTitle;
    firstRender = false;
  }

  render(data);

  function poll() {
    fetch("/api/status/" + encodeURIComponent(token), { cache: "no-store" })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (s) { if (s) render(s); })
      .catch(function () {})
      .then(function () { setTimeout(poll, 5000); });
  }
  setTimeout(poll, 5000);

  window.addEventListener("focus", function () { document.title = baseTitle; });
})();
