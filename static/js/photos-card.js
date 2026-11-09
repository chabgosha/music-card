/* Страница получателя: отмечаем, когда конверт открыли, и «Ответить улыбкой» — селфи системной камерой.
   Камера открывается через <input type="file" capture="user">: это работает и по обычному http. */
(function () {
  "use strict";

  var $ = function (sel) { return document.querySelector(sel); };
  var stage = $("[data-card-code]");
  if (!stage) return;
  var code = stage.dataset.cardCode;

  // ----- открыли конверт -----
  var openBtn = $("#open-btn");
  if (openBtn) {
    openBtn.addEventListener("click", function () {
      var url = "/c/" + encodeURIComponent(code) + "/seen";
      if (navigator.sendBeacon) navigator.sendBeacon(url);
      else fetch(url, { method: "POST", keepalive: true }).catch(function () {});
    });
  }

  // ----- ответ улыбкой -----
  var box = $("#smile");
  if (!box) return;
  var consent = $("#smile-consent");
  var btn = $("#smile-btn");
  var input = $("#smile-file");
  var status = $("#smile-status");
  var sent = $("#smile-sent");

  function setStatus(text, tone) {
    status.textContent = text || "";
    if (tone) status.dataset.tone = tone; else delete status.dataset.tone;
  }

  btn.addEventListener("click", function () {
    if (!consent.checked) {
      setStatus("Сначала отметьте согласие отправить фото.", "error");
      consent.focus();
      return;
    }
    setStatus("");
    input.click();  // снимок делается только по нажатию — камера сама не включается
  });

  consent.addEventListener("change", function () {
    if (consent.checked && status.dataset.tone === "error") setStatus("");
  });

  input.addEventListener("change", function () {
    var file = input.files[0];
    input.value = "";
    if (!file) return;
    btn.disabled = true;
    setStatus("Отправляем улыбку…", "busy");
    var preview = null;
    window.PhotoUtils.shrinkPhoto(file).then(function (blob) {
      preview = URL.createObjectURL(blob);
      var fd = new FormData();
      fd.append("consent", "1");
      fd.append("file", blob, "smile.jpg");
      return fetch("/c/" + encodeURIComponent(code) + "/replies", { method: "POST", body: fd }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) throw new Error(data.error || "Сервер ответил ошибкой " + res.status + ".");
        });
      }, function () { throw new Error("Нет связи. Проверьте интернет и попробуйте ещё раз."); });
    }).then(function () {
      sent.querySelector("img").src = preview;
      sent.hidden = false;
      setStatus(box.dataset.thanks, "ok");
      btn.textContent = "Отправить ещё одну";
    }).catch(function (err) {
      setStatus(err.message, "error");
    }).then(function () { btn.disabled = false; });
  });
})();
