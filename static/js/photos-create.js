/* Конструктор: фото на лицевую сторону открытки и ссылка на статус после создания.
   create.js берёт id фото из window.cardPhotoId и сообщает о готовой открытке событием card:created. */
(function () {
  "use strict";

  var $ = function (sel) { return document.querySelector(sel); };
  var input = $("#photo-file");
  if (!input) return;

  var consent = $("#photo-consent");
  var status = $("#photo-status");
  var removeBtn = $("#photo-remove");
  var pickTitle = $("#photo-pick-title");
  var frame = $("[data-postcard] [data-pc-photo]");
  var img = $("[data-postcard] [data-pc=\"photo\"]");

  window.cardPhotoId = null;
  var ownPhoto = false;  // фото загрузил сам человек (картинка от ИИ его не заменяет)

  function setStatus(text, tone) {
    status.textContent = text || "";
    if (tone) status.dataset.tone = tone; else delete status.dataset.tone;
  }

  function showPhoto(url) {
    img.src = url || "";
    frame.hidden = !url;
    removeBtn.hidden = !url;
    pickTitle.textContent = url ? "Выбрать другое фото" : "Выбрать фото";
  }

  input.addEventListener("change", function () {
    var file = input.files[0];
    input.value = "";
    if (!file) return;
    if (!consent.checked) {
      setStatus("Отметьте согласие: на фото вы или люди, которые не против.", "error");
      consent.focus();
      return;
    }
    setStatus("Готовим фото…", "busy");
    window.PhotoUtils.shrinkPhoto(file).then(function (blob) {
      var fd = new FormData();
      fd.append("consent", "1");
      fd.append("file", blob, "photo.jpg");
      return fetch("/api/photos", { method: "POST", body: fd }).then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) throw new Error(data.error || "Сервер ответил ошибкой " + res.status + ".");
          return data;
        });
      }, function () { throw new Error("Нет связи с сервером. Проверьте, что программа запущена."); });
    }).then(function (data) {
      window.cardPhotoId = data.id;
      ownPhoto = true;
      showPhoto(data.url);
      setStatus("Фото на открытке.", "ok");
    }).catch(function (err) {
      setStatus(err.message, "error");
    });
  });

  removeBtn.addEventListener("click", function () {
    window.cardPhotoId = null;
    ownPhoto = false;
    showPhoto(null);
    setStatus("");
  });

  // ---------- картинка от ИИ встаёт на место фото (её рисует create.js, раздел 0) ----------
  window.CardPhoto = {
    hasOwn: function () { return ownPhoto; },
    showAi: function (id, url) {
      window.cardPhotoId = id;
      ownPhoto = false;
      showPhoto(url);
      setStatus("На открытке картинка от ИИ. Можно заменить своим фото.", "ok");
    }
  };

  // ---------- после создания: ссылка на статус для отправителя ----------
  document.addEventListener("card:created", function (e) {
    var url = e.detail && e.detail.status_url;
    var box = $("#result-status");
    if (!url || !box) return;
    $("#status-link").href = url;
    $("#status-url").textContent = url;
    box.hidden = false;
  });
})();
