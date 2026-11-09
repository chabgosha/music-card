/* Общее для фото: уменьшаем снимок в браузере и пересохраняем в JPEG.
   Так на сервер уходит лёгкий файл без EXIF (геолокации, модели телефона), а снимок
   с телефона, повёрнутый «на бок», встаёт правильно. */
(function () {
  "use strict";

  var MAX_SIDE = 1280;
  var PASS_THROUGH = ["image/jpeg", "image/png", "image/webp"];

  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () { resolve({ img: img, url: url }); };
      img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("decode")); };
      img.src = url;
    });
  }

  function shrinkPhoto(file) {
    if (file.type && file.type.indexOf("image/") !== 0) {
      return Promise.reject(new Error("Это не фото. Выберите снимок в формате JPG или PNG."));
    }
    return loadImage(file).then(function (loaded) {
      var img = loaded.img;
      var w = img.naturalWidth, h = img.naturalHeight;
      var k = Math.min(1, MAX_SIDE / Math.max(w, h));
      var canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(w * k));
      canvas.height = Math.max(1, Math.round(h * k));
      var ctx = canvas.getContext("2d");
      ctx.fillStyle = "#fff";  // прозрачный PNG — на белом, как на фотобумаге
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(loaded.url);
      return new Promise(function (resolve, reject) {
        canvas.toBlob(function (blob) {
          if (blob) resolve(blob); else reject(new Error("encode"));
        }, "image/jpeg", 0.86);
      });
    }).catch(function () {
      // браузер не смог открыть снимок сам — отправим как есть, если формат обычный
      if (PASS_THROUGH.indexOf(file.type) >= 0) return file;
      throw new Error("Не получилось открыть это фото. Попробуйте снимок в формате JPG или PNG.");
    });
  }

  window.PhotoUtils = { shrinkPhoto: shrinkPhoto };
})();
