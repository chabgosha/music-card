/* Конструктор открытки: повод → текст → голос → музыка → громкость → создать. */
(function () {
  "use strict";

  var cfg = window.CARD_CONFIG;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  var form = $("#card-form");
  var card = $("[data-postcard]");
  var pc = {
    occasion: $('[data-pc="occasion"]', card),
    stamp: $('[data-pc="stamp"]', card),
    recipient: $('[data-pc="recipient"]', card),
    message: $('[data-pc="message"]', card),
    sender: $('[data-pc="sender"]', card),
    postmark: $('[data-pc="postmark"]', card)
  };

  var state = {
    theme: Object.keys(cfg.themes)[0],
    voiceId: null,
    voiceMs: 0,
    track: null,
    trackManual: false,
    previewKey: null
  };

  // ---------- общие помощники ----------
  function setStatus(el, text, tone) {
    el.textContent = text || "";
    if (tone) el.dataset.tone = tone; else delete el.dataset.tone;
  }

  function fmtTime(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function api(url, options) {
    return fetch(url, options).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) throw new Error(data.error || "Сервер ответил ошибкой " + res.status + ".");
        return data;
      });
    }, function () {
      throw new Error("Нет связи с сервером. Проверьте, что программа запущена.");
    });
  }

  function mixParams() {
    return {
      voice_id: state.voiceId,
      track: state.track || "",
      voice_volume: Number(form.voice_volume.value),
      music_volume: Number(form.music_volume.value)
    };
  }

  // ---------- ДИНАМИЧЕСКАЯ ТЕМА И ЧАСТИЦЫ ----------
  function createParticles(emoji, color, count) {
    count = count || 40;
    var container = document.getElementById('animatedBg');
    if (!container) return;
    container.innerHTML = '';
    for (var i = 0; i < count; i++) {
      var particle = document.createElement('div');
      particle.classList.add('particle');
      particle.textContent = emoji;
      particle.style.color = color;
      particle.style.left = Math.random() * 100 + '%';
      particle.style.fontSize = (1 + Math.random() * 2) + 'rem';
      particle.style.animationDuration = (5 + Math.random() * 10) + 's';
      particle.style.animationDelay = Math.random() * 5 + 's';
      container.appendChild(particle);
    }
  }

  // «Свой повод»: цифровой дождь — вертикальные строки из 0 и 1 (идея и стиль — наш дизайнер)
  function createBinaryParticles(count) {
    var container = document.getElementById('animatedBg');
    if (!container) return;
    container.innerHTML = '';
    for (var i = 0; i < count; i++) {
      var particle = document.createElement('div');
      particle.classList.add('particle');
      var str = '';
      var len = 5 + Math.floor(Math.random() * 6);
      for (var j = 0; j < len; j++) str += Math.random() < 0.5 ? '0' : '1';
      particle.textContent = str;
      particle.style.color = '#00E5FF';
      particle.style.left = Math.random() * 100 + '%';
      particle.style.fontSize = '1.2rem';
      particle.style.animationDuration = (4 + Math.random() * 6) + 's';
      particle.style.animationDelay = Math.random() * 5 + 's';
      container.appendChild(particle);
    }
  }

  function applyVisualTheme(themeKey) {
    var theme = cfg.themes[themeKey];
    if (!theme) return;
    document.body.dataset.theme = themeKey;
    document.body.style.setProperty('--theme-bg', theme.bg);
    if (themeKey === 'custom') createBinaryParticles(60);
    else createParticles(theme.particle, theme.particle_color, 40);
  }

  // ---------- 1. Повод ----------
  function applyTheme(key) {
    var t = cfg.themes[key];
    state.theme = key;
    $$(".theme-chip").forEach(function (chip) {
      chip.setAttribute("aria-checked", chip.dataset.theme === key ? "true" : "false");
    });
    card.style.setProperty("--face", t.face);
    card.style.setProperty("--face-ink", t.ink);
    pc.occasion.textContent = t.title;
    pc.stamp.textContent = t.stamp;
    form.message.placeholder = t.hint;
    pc.message.dataset.placeholder = t.hint;
    if (!state.trackManual) selectTrack("lib:" + t.track, false);
    // ↓↓↓ ДОБАВЛЯЕМ ВЫЗОВ ВИЗУАЛЬНОЙ ТЕМЫ ЗДЕСЬ
    applyVisualTheme(key);
  }

  $$(".theme-chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      applyTheme(chip.dataset.theme);
      showAiPanel(chip.dataset.theme === "custom");  // панель ИИ открыта только у «Свой повод»
    });
  });

  // ---------- 2. Текст ----------
  var counter = $("#msg-counter");
  function messageSize(text) {
    var weight = text.length + (text.split("\n").length - 1) * 25;
    return weight > 240 ? "xl" : weight > 170 ? "l" : weight > 100 ? "m" : "";
  }
  function syncText() {
    pc.recipient.textContent = form.recipient.value.trim();
    pc.sender.textContent = form.sender.value.trim();
    pc.message.textContent = form.message.value.trim();
    card.dataset.size = messageSize(form.message.value.trim());
    counter.textContent = form.message.value.length + " / " + cfg.maxMessage;
    updateCreateState();
  }
  ["recipient", "sender", "message"].forEach(function (name) {
    form[name].addEventListener("input", syncText);
  });

  function updatePostmark() {
    if (!state.voiceId) { pc.postmark.textContent = ""; return; }
    var title = "";
    var picked = $('.track [aria-checked="true"] .track__title');
    if (picked) title = picked.textContent;
    pc.postmark.textContent = fmtTime(state.voiceMs + 5000) + (title ? "\n" + title : "");
  }

  // ---------- 3. Голос ----------
  var consent = $("#voice-consent");
  var voiceStatus = $("#voice-status");
  var voiceReady = $("#voice-ready");
  var voiceAudio = $("#voice-audio");
  var recBtn = $("#rec-btn");
  var recTime = $("#rec-time");
  var recMeter = $("#rec-meter");
  var recHint = $("#rec-hint");
  var voiceFile = $("#voice-file");
  var canRecord = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder && window.isSecureContext);

  function showVoiceTab(name) {
    $$("[data-voice-tab]").forEach(function (b) { b.setAttribute("aria-selected", b.dataset.voiceTab === name ? "true" : "false"); });
    $$("[data-voice-panel]").forEach(function (p) { p.hidden = p.dataset.voicePanel !== name; });
  }
  $$("[data-voice-tab]").forEach(function (b) {
    b.addEventListener("click", function () { if (!b.disabled) showVoiceTab(b.dataset.voiceTab); });
  });
  if (!canRecord) {
    $('[data-voice-tab="record"]').disabled = true;
    showVoiceTab("upload");
    setStatus(voiceStatus, window.isSecureContext
      ? "Этот браузер не умеет записывать звук. Загрузите файл — например, голосовое из диктофона."
      : "Записать голос здесь нельзя: браузер включает микрофон только по защищённой ссылке (https://…). Загрузите файл — например, голосовое из диктофона телефона.");
  }

  function requireConsent() {
    if (consent.checked) return true;
    setStatus(voiceStatus, "Сначала отметьте согласие на использование записи голоса.", "error");
    consent.focus();
    return false;
  }

  function uploadVoice(blob, filename, source, localUrl) {
    var fd = new FormData();
    fd.append("kind", "voice");
    fd.append("consent", "1");
    fd.append("source", source);
    fd.append("file", blob, filename);
    setStatus(voiceStatus, "Обрабатываем запись…", "busy");
    recBtn.disabled = true;
    return api("/api/uploads", { method: "POST", body: fd }).then(function (data) {
      state.voiceId = data.id;
      state.voiceMs = data.duration_ms;
      state.previewKey = null;
      voiceAudio.src = localUrl;
      voiceReady.hidden = false;
      setStatus(voiceStatus, "Голос добавлен: " + fmtTime(data.duration_ms) + ". Можно переходить к музыке.", "ok");
      updatePostmark();
      updateCreateState();
    }).catch(function (err) {
      setStatus(voiceStatus, err.message, "error");
    }).then(function () { recBtn.disabled = false; });
  }

  var rec = null;
  function startRecording() {
    if (!requireConsent()) return;
    navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } })
      .then(function (stream) {
        var types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
        var mime = types.filter(function (t) { return MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t); })[0];
        var mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
        var chunks = [];
        var Ctx = window.AudioContext || window.webkitAudioContext;
        var ctx = Ctx ? new Ctx() : null;
        var analyser = null, buf = null;
        if (ctx) {
          analyser = ctx.createAnalyser();
          analyser.fftSize = 512;
          buf = new Uint8Array(analyser.fftSize);
          ctx.createMediaStreamSource(stream).connect(analyser);
        }
        rec = { mr: mr, stream: stream, ctx: ctx, started: Date.now(), raf: 0, timer: 0 };

        mr.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
        mr.onstop = function () {
          var type = mr.mimeType || mime || "audio/webm";
          var blob = new Blob(chunks, { type: type });
          var ext = type.indexOf("mp4") >= 0 ? ".m4a" : type.indexOf("ogg") >= 0 ? ".ogg" : ".webm";
          if (blob.size < 2000) {
            setStatus(voiceStatus, "Запись получилась пустой. Попробуйте ещё раз и говорите чуть громче.", "error");
            return;
          }
          uploadVoice(blob, "voice" + ext, "record", URL.createObjectURL(blob));
        };
        mr.start(250);

        recBtn.dataset.state = "recording";
        recBtn.setAttribute("aria-label", "Остановить запись");
        recHint.textContent = "Идёт запись. Нажмите ещё раз, чтобы остановить.";
        setStatus(voiceStatus, "");

        rec.timer = setInterval(function () {
          var ms = Date.now() - rec.started;
          recTime.textContent = fmtTime(ms);
          if (ms >= cfg.maxRecordSeconds * 1000) stopRecording();
        }, 200);
        (function loop() {
          if (!rec) return;
          if (analyser) {
            analyser.getByteTimeDomainData(buf);
            var sum = 0;
            for (var i = 0; i < buf.length; i++) { var v = (buf[i] - 128) / 128; sum += v * v; }
            recMeter.style.width = Math.min(100, Math.sqrt(sum / buf.length) * 320) + "%";
          }
          rec.raf = requestAnimationFrame(loop);
        })();
      })
      .catch(function (err) {
        var msg = err && err.name === "NotAllowedError"
          ? "Браузер не дал доступ к микрофону. Разрешите его (значок слева от адреса) и нажмите запись снова."
          : err && err.name === "NotFoundError"
            ? "Микрофон не найден. Подключите его или загрузите готовый файл."
            : "Не получилось включить микрофон. Попробуйте загрузить файл.";
        setStatus(voiceStatus, msg, "error");
      });
  }

  function stopRecording() {
    if (!rec) return;
    var r = rec;
    rec = null;
    clearInterval(r.timer);
    cancelAnimationFrame(r.raf);
    if (r.mr.state !== "inactive") r.mr.stop();
    r.stream.getTracks().forEach(function (t) { t.stop(); });
    if (r.ctx) r.ctx.close();
    recMeter.style.width = "0";
    recBtn.dataset.state = "";
    recBtn.setAttribute("aria-label", "Начать запись");
    recHint.textContent = "Нажмите и скажите поздравление. До 3 минут.";
  }

  recBtn.addEventListener("click", function () { if (rec) stopRecording(); else startRecording(); });

  voiceFile.addEventListener("change", function () {
    var file = voiceFile.files[0];
    if (!file) return;
    if (!requireConsent()) { voiceFile.value = ""; return; }
    uploadVoice(file, file.name, "file", URL.createObjectURL(file));
    voiceFile.value = "";
  });

  $("#voice-reset").addEventListener("click", function () {
    state.voiceId = null;
    state.voiceMs = 0;
    state.previewKey = null;
    voiceAudio.pause();
    voiceAudio.removeAttribute("src");
    voiceReady.hidden = true;
    recTime.textContent = "0:00";
    setStatus(voiceStatus, "");
    updatePostmark();
    updateCreateState();
  });

  // ---------- 4. Музыка ----------
  var sampler = new Audio();
  var playingBtn = null;
  function stopSampler() {
    sampler.pause();
    if (playingBtn) playingBtn.dataset.playing = "false";
    playingBtn = null;
  }
  sampler.addEventListener("ended", stopSampler);

  function selectTrack(id, manual) {
    state.track = id;
    if (manual) state.trackManual = true;
    $$(".track").forEach(function (li) {
      $(".track__pick", li).setAttribute("aria-checked", li.dataset.track === id ? "true" : "false");
    });
    state.previewKey = null;
    updatePostmark();
  }

  $("#tracks").addEventListener("click", function (e) {
    var li = e.target.closest(".track");
    if (!li) return;
    if (e.target.closest(".track__pick")) { selectTrack(li.dataset.track, true); return; }
    var play = e.target.closest(".track__play");
    if (play) {
      if (playingBtn === play) { stopSampler(); return; }
      stopSampler();
      sampler.src = play.dataset.play;
      sampler.play().catch(function () {});
      play.dataset.playing = "true";
      playingBtn = play;
    }
  });

  var musicRights = $("#music-rights");
  var musicFile = $("#music-file");
  var musicStatus = $("#music-status");
  musicFile.addEventListener("change", function () {
    var file = musicFile.files[0];
    if (!file) return;
    if (!musicRights.checked) {
      setStatus(musicStatus, "Отметьте, что у вас есть права на этот трек.", "error");
      musicRights.focus();
      musicFile.value = "";
      return;
    }
    var fd = new FormData();
    fd.append("kind", "music");
    fd.append("rights", "1");
    fd.append("file", file, file.name);
    setStatus(musicStatus, "Загружаем трек…", "busy");
    api("/api/uploads", { method: "POST", body: fd }).then(function (data) {
      var own = $("#own-track");
      own.dataset.track = data.id;
      $(".track__play", own).dataset.play = URL.createObjectURL(file);
      $("#own-track-title").textContent = data.title || "Мой трек";
      own.hidden = false;
      selectTrack(data.id, true);
      setStatus(musicStatus, "Трек загружен и выбран.", "ok");
    }).catch(function (err) {
      setStatus(musicStatus, err.message, "error");
    });
    musicFile.value = "";
  });

  // ---------- 4б. Своя мелодия по настроению ----------
  var maker = $("#melody-maker");
  var genLi = $("#gen-track");
  var melodyAgain = $("#melody-again");
  var melodyName = $("#melody-name");
  var melodyStatus = $("#melody-status");
  var melody = { mood: null, seed: null, variant: 0, req: 0 };

  function playSample(btn) {
    stopSampler();
    sampler.src = btn.dataset.play;
    sampler.play().catch(function () {});
    btn.dataset.playing = "true";
    playingBtn = btn;
  }

  function composeMelody(seed, autoplay) {
    if (!melody.mood) return;
    var req = ++melody.req;
    var name = melodyName.checked ? form.recipient.value.trim() : "";
    $$(".mood-chip").forEach(function (c) {
      c.setAttribute("aria-checked", c.dataset.mood === melody.mood ? "true" : "false");
    });
    melodyAgain.disabled = true;
    setStatus(melodyStatus, "Сочиняем мелодию…", "busy");
    api("/api/melody", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mood: melody.mood, seed: seed, name: name })
    }).then(function (data) {
      if (req !== melody.req) return;
      if (data.seed !== melody.seed) melody.variant += 1;
      melody.seed = data.seed;
      genLi.dataset.track = data.id;
      $(".track__play", genLi).dataset.play = data.url;
      $("#gen-track-title").textContent = data.title;
      $("#gen-track-mood").textContent = data.about + " · вариант " + melody.variant;
      genLi.hidden = false;
      selectTrack(data.id, true);
      if (autoplay) playSample($(".track__play", genLi));
      setStatus(melodyStatus, name && melodyName.checked
        ? "Мелодия начинается с мотива из имени. Не нравится — «Ещё вариант»."
        : "Мелодия готова и выбрана. Не нравится — «Ещё вариант».", "ok");
    }).catch(function (err) {
      if (req === melody.req) setStatus(melodyStatus, err.message, "error");
    }).then(function () {
      if (req === melody.req) melodyAgain.disabled = false;
    });
  }

  function randomSeed() { return Math.floor(Math.random() * 1000000); }

  $$(".mood-chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      melody.mood = chip.dataset.mood;
      composeMelody(randomSeed(), true);
    });
  });
  melodyAgain.addEventListener("click", function () { composeMelody(randomSeed(), true); });
  maker.addEventListener("toggle", function () {
    if (maker.open && !melody.mood) {
      melody.mood = cfg.themeMood[state.theme] || "happy";
      composeMelody(randomSeed(), true);
    }
  });
  function refreshNameMotif() {
    if (melody.mood && melody.seed !== null && state.track === genLi.dataset.track) composeMelody(melody.seed, false);
  }
  melodyName.addEventListener("change", refreshNameMotif);
  form.recipient.addEventListener("change", function () { if (melodyName.checked) refreshNameMotif(); });

  // ---------- 5. Громкость и прослушивание ----------
  $$(".slider").forEach(function (s) {
    var input = $("input", s), out = $("output", s);
    input.addEventListener("input", function () { out.textContent = input.value + "%"; });
  });

  var previewBtn = $("#preview-btn");
  var previewAudio = $("#preview-audio");
  var previewStatus = $("#preview-status");
  // Сведённый звук: один и тот же для кнопки «Прослушать» и полного предпросмотра.
  // Если голос, музыка и громкость не менялись — второй раз сервер не дёргаем.
  var mix = { key: null, url: null };
  function getMix() {
    var params = mixParams();
    var key = JSON.stringify(params);
    if (key === mix.key && mix.url) return Promise.resolve({ url: mix.url, fresh: false });
    return api("/api/preview", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(params)
    }).then(function (data) {
      mix.key = key;
      mix.url = data.url;
      return { url: data.url, fresh: true };
    });
  }

  previewBtn.addEventListener("click", function () {
    stopSampler();
    var key = JSON.stringify(mixParams());
    if (key === state.previewKey && previewAudio.src) {
      previewAudio.currentTime = 0;
      previewAudio.play().catch(function () {});
      return;
    }
    previewBtn.disabled = true;
    setStatus(previewStatus, "Сводим голос и музыку…", "busy");
    getMix().then(function (data) {
      state.previewKey = key;
      previewAudio.src = data.url;
      previewAudio.hidden = false;
      previewAudio.play().catch(function () {});
      setStatus(previewStatus, "Так открытка прозвучит у получателя. Двигайте ползунки и слушайте снова.", "ok");
    }).catch(function (err) {
      setStatus(previewStatus, err.message, "error");
    }).then(function () { previewBtn.disabled = !state.voiceId; });
  });

  // ---------- 6. Создание ----------
  var createBtn = $("#create-btn");
  var createStatus = $("#create-status");

  function updateCreateState(keepStatus) {
    var hasText = form.message.value.trim().length > 0;
    previewBtn.disabled = !state.voiceId;
    createBtn.disabled = !(hasText && state.voiceId);
    if (keepStatus === true || createStatus.dataset.tone === "busy") return;
    var missing = [];
    if (!hasText) missing.push("текст");
    if (!state.voiceId) missing.push("голос");
    setStatus(createStatus, missing.length ? "Чтобы создать открытку, добавьте " + missing.join(" и ") + "." : "");
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (createBtn.disabled) return;
    stopSampler();
    var openAt = $("#open-at").value;
    var payload = mixParams();
    payload.theme = state.theme;
    payload.recipient = form.recipient.value;
    payload.sender = form.sender.value;
    payload.message = form.message.value;
    payload.open_at = openAt ? new Date(openAt).toISOString() : null;
    payload.photo_id = window.cardPhotoId || null;

    createBtn.disabled = true;
    setStatus(createStatus, "Создаём открытку…", "busy");
    api("/api/cards", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload)
    }).then(function (data) {
      setStatus(createStatus, "Открытка создана. Ссылка и QR-код — рядом с открыткой.", "ok");
      showResult(data);
      document.dispatchEvent(new CustomEvent("card:created", { detail: data }));
    }).catch(function (err) {
      setStatus(createStatus, err.message, "error");
    }).then(function () { updateCreateState(true); });
  });

  function showResult(data) {
    var result = $("#result");
    $("#result-url").value = data.url;
    $("#result-qr").src = data.qr_url;
    $("#open-link").href = data.url;
    var note = $("#result-note");
    if (/\/\/127\.0\.0\.1/.test(data.url)) {
      note.textContent = "Компьютер не подключён к сети, поэтому ссылка откроется только на нём. Подключитесь к Wi-Fi или раздаче и создайте открытку снова.";
    } else if (data.open_at) {
      note.textContent = "Откроется " + new Date(data.open_at).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) + ". Отсканируйте QR телефоном в той же сети.";
    } else {
      note.textContent = "Отсканируйте QR телефоном, подключённым к той же сети.";
    }
    result.hidden = false;
    result.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  $("#copy-btn").addEventListener("click", function () {
    var input = $("#result-url");
    var btn = this;
    var done = function () { btn.textContent = "Скопировано"; setTimeout(function () { btn.textContent = "Скопировать ссылку"; }, 1600); };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(input.value).then(done, function () { input.select(); document.execCommand("copy"); done(); });
    } else {
      input.select();
      document.execCommand("copy");
      done();
    }
  });

  $("#again-btn").addEventListener("click", function () { window.location.href = "/"; });

  // ---------- 7. Полный предпросмотр ----------
  // Открытка крупно, звук и сводка — в одном окне на весь экран, без прокрутки.
  var fp = $("#full-preview");
  var fpCard = $("#fp-card");
  var fpAudio = $("#fp-audio");
  var fpSoundNote = $("#fp-sound-note");
  var fpMissing = $("#fp-missing");
  var fpCreate = $("#fp-create");
  var fpReq = 0;

  function fpSet(id, text, empty) {
    var el = $(id);
    el.textContent = text || empty;
    el.classList.toggle("fp-empty", !text);
  }

  function openFullPreview() {
    stopSampler();
    previewAudio.pause();
    // копия живой открытки из правой колонки: тот же повод, текст, фото и штемпель
    var copy = card.closest(".airmail").cloneNode(true);
    copy.classList.remove("airmail--preview");
    $("[data-postcard]", copy).removeAttribute("data-postcard");
    fpCard.innerHTML = "";
    fpCard.appendChild(copy);

    var picked = $('.track [aria-checked="true"] .track__title');
    var openAt = $("#open-at").value;
    fpSet("#fp-theme", cfg.themes[state.theme].stamp + " " + cfg.themes[state.theme].title, "");
    fpSet("#fp-recipient", form.recipient.value.trim(), "не указано");
    fpSet("#fp-sender", form.sender.value.trim(), "не указано");
    fpSet("#fp-music", picked ? picked.textContent : "", "без музыки");
    fpSet("#fp-open", openAt
      ? new Date(openAt).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })
      : "сразу", "сразу");

    var missing = [];
    if (!form.message.value.trim()) missing.push("текст поздравления");
    if (!state.voiceId) missing.push("голос");
    fpMissing.textContent = missing.length ? "Чтобы создать открытку, добавьте " + missing.join(" и ") + "." : "";
    fpMissing.hidden = !missing.length;
    fpCreate.disabled = createBtn.disabled;

    var req = ++fpReq;
    fpAudio.pause();
    if (!state.voiceId) {
      fpAudio.hidden = true;
      fpAudio.removeAttribute("src");
      setStatus(fpSoundNote, "Звука пока нет: запишите или загрузите голос в шаге 3.");
    } else {
      setStatus(fpSoundNote, "Сводим голос и музыку…", "busy");
      getMix().then(function (data) {
        if (req !== fpReq || !fp.open) return;
        if (fpAudio.getAttribute("src") !== data.url) fpAudio.src = data.url;
        fpAudio.hidden = false;
        fpAudio.currentTime = 0;
        fpAudio.play().catch(function () {});
        setStatus(fpSoundNote, "Голос и музыка — так, как услышит получатель.", "ok");
      }).catch(function (err) {
        if (req === fpReq) setStatus(fpSoundNote, err.message, "error");
      });
    }

    if (fp.showModal) fp.showModal(); else fp.setAttribute("open", "");
    $("#fp-back").focus();
  }

  function closeFullPreview() {
    fpAudio.pause();
    if (fp.close) fp.close(); else fp.removeAttribute("open");
  }

  $$("[data-full-preview]").forEach(function (b) { b.addEventListener("click", openFullPreview); });
  $("#fp-back").addEventListener("click", closeFullPreview);
  fp.addEventListener("close", function () { fpAudio.pause(); });  // Esc тоже закрывает окно
  fpCreate.addEventListener("click", function () {
    if (createBtn.disabled) return;
    closeFullPreview();
    if (form.requestSubmit) form.requestSubmit(createBtn);
    else form.dispatchEvent(new Event("submit", { cancelable: true }));
  });

  // ---------- 1б. «Свой повод»: открытка из одной фразы (ИИ) ----------
  // ИИ сам выбирает повод: если он подходит под обычный, фон страницы меняется на него,
  // если нет — остаётся «Свой повод» с цифровым дождём.
  var aiPanel = $("#ai-panel");
  function showAiPanel(open) {
    if (aiPanel.hidden === !open) return;
    aiPanel.hidden = !open;
    if (open) $("#ai-prompt").focus();
  }
  var aiPrompt = $("#ai-prompt");
  var aiGo = $("#ai-go");
  var aiAgain = $("#ai-again");
  var aiStatus = $("#ai-status");
  var aiDraw = $("#ai-draw");
  var aiRedraw = $("#ai-redraw");
  var aiDrawStatus = $("#ai-draw-status");
  var aiLast = "";     // прошлый текст ИИ — для «Другой текст»
  var aiPicture = "";  // что нарисовать: описание картинки от ИИ
  var drawReq = 0;

  function moodTitle(id) {
    var chip = $('.mood-chip[data-mood="' + id + '"]');
    return chip ? chip.lastChild.textContent.trim().toLowerCase() : "";  // без эмодзи в начале
  }

  function applyAiCard(d) {
    applyTheme(d.occasion);
    if (d.recipient) form.recipient.value = d.recipient;
    if (d.sender) form.sender.value = d.sender;
    form.message.value = d.message;
    syncText();
    melody.mood = d.mood;
    maker.open = true;
    composeMelody(randomSeed(), false);
    aiLast = d.message;
    aiPicture = d.picture || "";
  }

  function drawPicture() {
    if (!aiPicture) return;
    if (window.CardPhoto.hasOwn()) {
      setStatus(aiDrawStatus, "Картинку не рисуем: на открытке уже ваше фото.", "");
      return;
    }
    var req = ++drawReq;
    aiRedraw.disabled = true;
    setStatus(aiDrawStatus, "ИИ рисует картинку, это займёт около минуты…", "busy");
    api("/api/ai-picture", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description: aiPicture })
    }).then(function (d) {
      if (req !== drawReq) return;
      if (window.CardPhoto.hasOwn()) {
        setStatus(aiDrawStatus, "Картинка готова, но на открытке оставлено ваше фото.", "");
        return;
      }
      window.CardPhoto.showAi(d.id, d.url);
      aiRedraw.lastChild.textContent = " Другая картинка";
      setStatus(aiDrawStatus, "Картинка готова и стоит на лицевой стороне.", "ok");
    }).catch(function (err) {
      if (req === drawReq) setStatus(aiDrawStatus, err.message, "error");
    }).then(function () {
      if (req === drawReq) aiRedraw.disabled = false;
    });
  }

  function generateCard(again) {
    var prompt = aiPrompt.value.trim();
    if (prompt.length < 3) {
      setStatus(aiStatus, "Напишите пару слов: кому открытка и по какому поводу.", "error");
      aiPrompt.focus();
      return;
    }
    aiGo.disabled = aiAgain.disabled = true;
    setStatus(aiStatus, "ИИ пишет открытку…", "busy");
    api("/api/ai-card", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: prompt, previous: again ? aiLast : "" })
    }).then(function (d) {
      applyAiCard(d);
      var occasion = "повод «" + cfg.themes[d.occasion].title + "»", mood = moodTitle(d.mood);
      if (d.source === "ai") setStatus(aiStatus, "Готово: ИИ написал текст и выбрал " + occasion + " и " +
        mood.replace(/ая$/, "ую") + " мелодию. Всё можно поправить ниже.", "ok");
      else setStatus(aiStatus, d.note + " Выбран " + occasion + " и " + mood + " мелодия.", "");
      aiRedraw.hidden = !aiPicture;
      if (again) return;  // «Другой текст» картинку не перерисовывает
      if (aiDraw.checked && aiPicture) drawPicture();
      else if (aiDraw.checked) setStatus(aiDrawStatus, "Картинку рисует только ИИ, а сейчас он недоступен.", "");
    }).catch(function (err) {
      setStatus(aiStatus, err.message, "error");
    }).then(function () {
      aiGo.disabled = false;
      aiAgain.disabled = !aiLast;
    });
  }


  aiGo.addEventListener("click", function () { generateCard(false); });
  aiAgain.addEventListener("click", function () { generateCard(true); });
  aiRedraw.addEventListener("click", drawPicture);
  aiPrompt.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); generateCard(false); }
  });

  // ---------- старт ----------
  applyTheme(state.theme);
  syncText();
})();