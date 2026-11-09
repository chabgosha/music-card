"""
Музыкальная открытка — веб-сервер.

Запуск:  python app.py            (порт 5000)
         python app.py --port 8080
         python app.py --https    (для записи голоса с телефона; на Windows — run_https.bat)

Страницы:
  /            — конструктор открытки
  /c/<code>    — открытка для получателя
API (JSON):
  GET  /api/tracks     — подборка музыки
  POST /api/uploads    — загрузить голос или свою музыку (multipart)
  POST /api/preview    — свести предпросмотр
  POST /api/cards      — создать открытку
  POST /api/ai-card    — открытка из одной фразы (GigaChat, без сети — шаблоны), см. ai.py
  POST /api/ai-picture — картинка от ИИ на лицевую сторону (встаёт вместо фото)
Фото на открытке, ответы-селфи и страница статуса — в photos.py
"""
import argparse
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from flask import (Flask, abort, jsonify, render_template, request,
                   send_from_directory, url_for)
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

import ai
import composer
import mixer
import network
import photos
import storage

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MUSIC_DIR = os.path.join(BASE_DIR, "music")
MELODY_DIR = os.path.join(storage.DATA_DIR, "melodies")  # сочинённые мелодии (кэш, можно удалять)
os.makedirs(MELODY_DIR, exist_ok=True)

MAX_UPLOAD_MB = 40
MAX_MESSAGE = 300  # больше не помещается на открытку — остальное лучше сказать голосом
MAX_NAME = 40
ALLOWED_EXT = {".webm", ".ogg", ".oga", ".opus", ".mp3", ".wav", ".m4a", ".mp4",
               ".aac", ".flac", ".3gp", ".amr", ".caf"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.json.ensure_ascii = False

# ---------- поводы (оформление открытки) ----------
THEMES = OrderedDict([
    ("birthday", {"title": "День рождения", "stamp": "🎂", "track": "celebration",
                  "face": "#F6C445", "ink": "#3A2A05","bg": "#FFF8E7","particle": "✦","particle_color": "#F0C445",
                  "hint": "С днём рождения! Пусть этот год будет самым тёплым."}),
    ("newyear", {"title": "Новый год", "stamp": "❄️", "track": "snowflakes",
                 "face": "#1E5E4E", "ink": "#F3F0E6","bg": "#8B0000","particle": "❄","particle_color": "#FFFFFF",
                 "hint": "С Новым годом! Пусть сбудется всё задуманное."}),
    ("thanks", {"title": "Спасибо", "stamp": "💐", "track": "warm-evening",
                "face": "#2F5DA8", "ink": "#F2F5FB","bg": "#F0F8FF","particle": "✿", "particle_color": "#FFD700",
                "hint": "Спасибо, что ты рядом. Это много для меня значит."}),
    ("congrats", {"title": "Поздравляю", "stamp": "🎉", "track": "celebration",
                  "face": "#D9443A", "ink": "#FFF3EE", "bg": "#FFF0F0","particle": "✹","particle_color": "#D9443A",
                  "hint": "Поздравляю! Ты это заслужил(а)."}),
    ("love", {"title": "С любовью", "stamp": "💌", "track": "warm-evening",
              "face": "#E9A3B4", "ink": "#46121F","bg": "#FFF5F7","particle": "❤","particle_color": "#E9A3B4",
              "hint": "Просто хочу сказать, как ты мне дорог(а)."}),
    # свой повод: человек описывает его словами, ИИ пишет открытку (кнопка «Свой повод», фон — цифровой дождь)
    ("custom", {"title": "Для тебя", "stamp": "✨", "track": "celebration",
                "face": "#0B132B", "ink": "#FFFFFF", "bg": "#0B132B", "particle": "0", "particle_color": "#00E5FF",
                "hint": "Опишите повод выше — ИИ напишет поздравление. Или напишите своё."}),
])


def message_size(message):
    """Размер почерка на открытке по длине текста (то же правило в static/js/create.js)."""
    text = message or ""
    weight = len(text) + text.count("\n") * 25  # перенос строки «съедает» почти строку
    if weight > 240:
        return "xl"
    if weight > 170:
        return "l"
    if weight > 100:
        return "m"
    return ""


app.jinja_env.globals["message_size"] = message_size


def load_library():
    with open(os.path.join(MUSIC_DIR, "tracks.json"), encoding="utf-8") as f:
        tracks = json.load(f)
    return [t for t in tracks if os.path.exists(os.path.join(MUSIC_DIR, t["file"]))]


LIBRARY = load_library()
LIBRARY_BY_ID = {t["id"]: t for t in LIBRARY}

# ---------- кэш подготовленной музыки (чтобы не декодировать трек на каждый предпросмотр) ----------
_music_cache = OrderedDict()
_cache_lock = threading.Lock()


def music_segment(path):
    key = (path, os.path.getmtime(path))
    with _cache_lock:
        if key in _music_cache:
            _music_cache.move_to_end(key)
            return _music_cache[key]
    seg = mixer.prepare_music(path)
    with _cache_lock:
        _music_cache[key] = seg
        while len(_music_cache) > 6:
            _music_cache.popitem(last=False)
    return seg


# ---------- вспомогательное ----------

class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


@app.errorhandler(ApiError)
def handle_api_error(err):
    return jsonify(error=err.message), err.status


@app.errorhandler(mixer.AudioError)
def handle_audio_error(err):
    return jsonify(error=str(err)), 400


@app.errorhandler(RequestEntityTooLarge)
def handle_too_large(_err):
    return jsonify(error=f"Файл больше {MAX_UPLOAD_MB} МБ."), 413


@app.errorhandler(HTTPException)
def handle_http(err):
    if request.path.startswith("/api/"):
        return jsonify(error=err.description), err.code
    if err.code == 404:
        return render_template("not_found.html"), 404
    return err


def new_id(nbytes=6):
    return secrets.token_urlsafe(nbytes).replace("-", "x").replace("_", "z")


def public_base():
    """Адрес, по которому открытку откроют с другого устройства."""
    if os.environ.get("PUBLIC_URL"):
        return os.environ["PUBLIC_URL"].rstrip("/")
    host = request.host.split(":")[0]
    if host in ("localhost", "127.0.0.1", "::1"):
        port = request.host.split(":")[1] if ":" in request.host else "80"
        return f"{request.scheme}://{network.best_ip(app.config.get('LAN_IP'))}:{port}"
    return request.host_url.rstrip("/")


def clamp_int(value, lo, hi, default):
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def clean_name(value):
    """Имя в одну строку, без лишних пробелов."""
    return " ".join(str(value or "").split())[:MAX_NAME]


def clean_message(value):
    """Текст поздравления: сохраняем переносы строк, убираем пустые строки подряд."""
    lines = [line.rstrip() for line in str(value or "").replace("\r\n", "\n").split("\n")]
    text = "\n".join(lines).strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text[:MAX_MESSAGE]


def parse_melody(track):
    """'gen:<настроение>:<seed>[:<имя>]' → (mood, seed, name) или None."""
    parts = str(track or "").split(":", 3)
    if len(parts) < 3 or parts[0] != "gen" or parts[1] not in composer.MOODS:
        return None
    try:
        seed = int(parts[2])
    except ValueError:
        return None
    if not 0 <= seed < 10 ** 9:
        return None
    name = clean_name(parts[3]) if len(parts) == 4 else ""
    return parts[1], seed, name


def melody_id(mood, seed, name=""):
    return f"gen:{mood}:{seed}" + (f":{name}" if name else "")


def resolve_track(track):
    """'lib:<id>', 'up:<id>' или 'gen:…' → путь к файлу. Пустое значение = без музыки."""
    if not track:
        return None
    melody = parse_melody(track)
    if melody:
        return composer.render_file(MELODY_DIR, *melody)  # образец ~16 с, для прослушивания
    kind, _, tid = str(track).partition(":")
    if kind == "lib" and tid in LIBRARY_BY_ID:
        return os.path.join(MUSIC_DIR, LIBRARY_BY_ID[tid]["file"])
    if kind == "up":
        row = storage.get_upload(tid, "music")
        if row:
            return os.path.join(storage.UPLOAD_DIR, row["file"])
    raise ApiError("Выбранная музыка не найдена. Выберите трек ещё раз.")


def voice_path(voice_id):
    row = storage.get_upload(voice_id or "", "voice")
    if not row:
        raise ApiError("Голосовая запись не найдена. Запишите или загрузите её ещё раз.")
    return os.path.join(storage.UPLOAD_DIR, row["file"])


def mix_to_file(params, out_dir, name):
    """Сводит голос и музыку и сохраняет в out_dir/name.mp3. Возвращает (имя файла, длительность)."""
    voice = mixer.AudioSegment.from_wav(voice_path(params["voice_id"]))
    melody = parse_melody(params.get("track"))
    if melody:
        # своя мелодия сочиняется под длину голоса и заканчивается финальным аккордом
        voice_end = (mixer.INTRO_MS + len(voice)) / 1000
        track_path = composer.render_file(MELODY_DIR, *melody, fit_seconds=voice_end)
    else:
        track_path = resolve_track(params.get("track"))
    music = music_segment(track_path) if track_path else None
    mixed = mixer.build_card(voice, music, params["voice_volume"], params["music_volume"],
                             natural_end=bool(melody))
    out = mixer.encode_mp3(mixed, os.path.join(out_dir, name + ".mp3"))
    return os.path.basename(out), len(mixed)


def read_mix_params(data):
    return {
        "voice_id": str(data.get("voice_id") or ""),
        "track": str(data.get("track") or ""),
        "voice_volume": clamp_int(data.get("voice_volume"), 0, 100, 100),
        "music_volume": clamp_int(data.get("music_volume"), 0, 100, 35),
    }


def params_hash(p):
    raw = f'{p["voice_id"]}|{p["track"]}|{p["voice_volume"]}|{p["music_volume"]}'
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def cleanup_previews(max_age_hours=3):
    limit = time.time() - max_age_hours * 3600
    for folder in (storage.PREVIEW_DIR, MELODY_DIR):
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if os.path.getmtime(path) < limit:
                    os.remove(path)
            except OSError:
                pass


def parse_open_at(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ApiError("Не получилось разобрать дату открытия.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    now = datetime.now(timezone.utc)
    if dt <= now + timedelta(seconds=30):
        return None  # время уже прошло — откроется сразу
    if dt > now + timedelta(days=366):
        raise ApiError("Отложить открытку можно не больше чем на год.")
    return dt.isoformat()


# ---------- страницы ----------

@app.get("/")
def index():
    return render_template("index.html", themes=THEMES, library=LIBRARY,
                           moods=composer.public_moods(), theme_mood=composer.THEME_MOOD,
                           max_message=MAX_MESSAGE, max_name=MAX_NAME,
                           max_upload_mb=MAX_UPLOAD_MB)


@app.get("/c/<code>")
def card_page(code):
    card = storage.get_card(code)
    if not card:
        abort(404)
    theme = THEMES.get(card["theme"], THEMES["congrats"])
    locked = bool(card["open_at"]) and datetime.fromisoformat(card["open_at"]) > datetime.now(timezone.utc)
    return render_template("card.html", card=card, theme=theme, locked=locked,
                           audio_url=url_for("card_audio", code=code),
                           download_url=url_for("card_audio", code=code, download=1))


@app.get("/c/<code>/audio")
def card_audio(code):
    card = storage.get_card(code)
    if not card:
        abort(404)
    if card["open_at"] and datetime.fromisoformat(card["open_at"]) > datetime.now(timezone.utc):
        abort(403)
    download = request.args.get("download") == "1"
    if not download and request.headers.get("Range", "bytes=0-").startswith("bytes=0-"):
        storage.mark_opened(code)
    ext = os.path.splitext(card["audio_file"])[1]
    return send_from_directory(storage.CARD_DIR, card["audio_file"], as_attachment=download,
                               download_name=f"otkrytka-{code}{ext}", conditional=True)


@app.get("/c/<code>/qr.svg")
def card_qr(code):
    if not storage.get_card(code):
        abort(404)
    import qrcode
    from qrcode.image.svg import SvgPathImage
    img = qrcode.make(f"{public_base()}/c/{code}", image_factory=SvgPathImage, box_size=12, border=2)
    return app.response_class(img.to_string(encoding="unicode"), mimetype="image/svg+xml")


@app.get("/music/<path:filename>")
def library_music(filename):
    return send_from_directory(MUSIC_DIR, filename, conditional=True)


@app.get("/melody/<name>")
def melody_file(name):
    return send_from_directory(MELODY_DIR, name, conditional=True)


@app.get("/preview/<name>")
def preview_file(name):
    return send_from_directory(storage.PREVIEW_DIR, name, conditional=True)


# ---------- API ----------

@app.get("/api/tracks")
def api_tracks():
    return jsonify([
        {"id": f'lib:{t["id"]}', "title": t["title"], "mood": t.get("mood", ""),
         "url": url_for("library_music", filename=t["file"]), "license": t.get("license", "")}
        for t in LIBRARY
    ])


@app.post("/api/uploads")
def api_upload():
    kind = request.form.get("kind")
    if kind not in ("voice", "music"):
        raise ApiError("Неизвестный тип файла.")
    file = request.files.get("file")
    if not file:
        raise ApiError("Файл не получен.")
    if kind == "voice" and request.form.get("consent") != "1":
        raise ApiError("Подтвердите согласие на использование записи голоса.")
    if kind == "music" and request.form.get("rights") != "1":
        raise ApiError("Подтвердите, что у вас есть права на этот трек.")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        mime = (file.mimetype or "").split(";")[0]
        ext = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a",
               "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
               "audio/aac": ".aac"}.get(mime)
        if not ext:
            raise ApiError("Этот формат не подходит. Нужен аудиофайл: mp3, wav, m4a, ogg или webm.")

    upload_id = new_id()
    fd, tmp_path = tempfile.mkstemp(suffix=ext, dir=storage.UPLOAD_DIR)
    os.close(fd)
    try:
        file.save(tmp_path)
        if kind == "voice":
            seg = mixer.prepare_voice(tmp_path)
            stored = f"voice-{upload_id}.wav"
            seg.export(os.path.join(storage.UPLOAD_DIR, stored), format="wav")
            title = None
        else:
            seg = mixer.prepare_music(tmp_path)
            stored = f"music-{upload_id}{ext}"
            shutil.copyfile(tmp_path, os.path.join(storage.UPLOAD_DIR, stored))
            title = os.path.splitext(os.path.basename(file.filename or "Мой трек"))[0][:60] or "Мой трек"
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    storage.add_upload(upload_id, kind, stored, len(seg),
                       source=request.form.get("source", "file"),
                       consent=request.form.get("consent") == "1" or kind == "music",
                       title=title)
    return jsonify(id=(upload_id if kind == "voice" else f"up:{upload_id}"),
                   duration_ms=len(seg), title=title)


@app.get("/api/moods")
def api_moods():
    return jsonify(composer.public_moods())


@app.post("/api/melody")
def api_melody():
    """Сочинить мелодию: {mood, seed?, name?} → образец для прослушивания и id для открытки."""
    data = request.get_json(silent=True) or {}
    mood = data.get("mood")
    if mood not in composer.MOODS:
        raise ApiError("Выберите настроение мелодии.")
    seed = clamp_int(data.get("seed"), 0, 10 ** 9 - 1, secrets.randbelow(10 ** 6))
    name = clean_name(data.get("name"))
    path = composer.render_file(MELODY_DIR, mood, seed, name)
    info = composer.MOODS[mood]
    return jsonify(id=melody_id(mood, seed, name), seed=seed, mood=mood,
                   title=f'Своя мелодия · {info["title"]}',
                   about=info["about"] + (f", мотив из имени «{name}»" if name else ""),
                   url=url_for("melody_file", name=os.path.basename(path)))


@app.post("/api/ai-card")
def api_ai_card():
    """Открытка из одной фразы: {prompt, previous?} → повод, имена, настроение мелодии и текст."""
    data = request.get_json(silent=True) or {}
    try:
        card = ai.make_card(data.get("prompt"), data.get("previous", ""), MAX_MESSAGE)
    except ai.AIError as err:
        raise ApiError(str(err))
    if card["occasion"] not in THEMES:
        card["occasion"] = "congrats"
    return jsonify(card)


@app.post("/api/ai-picture")
def api_ai_picture():
    """Нарисовать картинку по описанию из /api/ai-card: {description} → id и адрес, как у загруженного фото."""
    data = request.get_json(silent=True) or {}
    try:
        picture = ai.draw_picture(data.get("description"))
    except ai.AIError as err:
        raise ApiError(str(err))
    photo_id = photos.store_photo("card", picture)
    return jsonify(id=photo_id, url=url_for("photos.draft_photo", photo_id=photo_id))


@app.post("/api/preview")
def api_preview():
    params = read_mix_params(request.get_json(silent=True) or {})
    cleanup_previews()
    name = "p-" + params_hash(params)
    for ext in (".mp3", ".m4a"):
        existing = os.path.join(storage.PREVIEW_DIR, name + ext)
        if os.path.exists(existing):  # такой предпросмотр уже сводили — отдаём готовый
            os.utime(existing)
            return jsonify(url=url_for("preview_file", name=name + ext))
    filename, duration = mix_to_file(params, storage.PREVIEW_DIR, name)
    return jsonify(url=url_for("preview_file", name=filename), duration_ms=duration)


@app.post("/api/cards")
def api_create_card():
    data = request.get_json(silent=True) or {}
    params = read_mix_params(data)
    theme = data.get("theme") if data.get("theme") in THEMES else "congrats"
    message = clean_message(data.get("message"))
    recipient = clean_name(data.get("recipient"))
    sender = clean_name(data.get("sender"))
    if not message:
        raise ApiError("Напишите текст поздравления.")
    open_at = parse_open_at(data.get("open_at"))

    code = new_id()
    filename, duration = mix_to_file(params, storage.CARD_DIR, f"card-{code}")
    storage.add_card(code=code, theme=theme, recipient=recipient, sender=sender, message=message,
                     voice_id=params["voice_id"], track_id=params["track"],
                     voice_volume=params["voice_volume"], music_volume=params["music_volume"],
                     audio_file=filename, duration_ms=duration, open_at=open_at)
    status_path = photos.on_card_created(code, data.get("photo_id"))
    page = f"{public_base()}/c/{code}"
    return jsonify(code=code, url=page, qr_url=url_for("card_qr", code=code),
                   open_at=open_at, status_url=public_base() + status_path), 201


# ---------- запуск ----------

def https_cert(ips):
    """Самоподписанный сертификат в certs/: создаётся один раз и потом не меняется,
    поэтому телефон спрашивает про «небезопасное подключение» только при первом заходе."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress

    cert_dir = os.path.join(BASE_DIR, "certs")
    cert_path, key_path = os.path.join(cert_dir, "https_cert.pem"), os.path.join(cert_dir, "https_key.pem")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path
    os.makedirs(cert_dir, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Музыкальная открытка")])
    alt = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    for ip in ips:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .sign(key, hashes.SHA256()))
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def main():
    parser = argparse.ArgumentParser(description="Музыкальная открытка")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    parser.add_argument("--https", action="store_true",
                        help="самоподписанный HTTPS (нужен для записи голоса на телефоне)")
    parser.add_argument("--ip", help="адрес компьютера в сети для ссылок, если выбран не тот "
                                     "(например: --ip 192.168.0.5)")
    args = parser.parse_args()

    storage.init()
    cleanup_previews()
    app.config["LAN_IP"] = args.ip
    scheme = "https" if args.https else "http"
    found = network.candidates()
    ip = network.best_ip(args.ip)
    print("=" * 64)
    print("  Музыкальная открытка запущена")
    print(f"  На этом компьютере:      {scheme}://localhost:{args.port}")
    print(f"  С телефона (та же сеть): {scheme}://{ip}:{args.port}")
    others = [(a, n, s) for a, n, s in found if a != ip]
    if others:
        print("  Другие адреса компьютера:")
        for addr, name, suspicious in others:
            note = " — похоже на VPN или виртуальный адаптер" if suspicious else ""
            print(f"    {addr}  ({name or 'адаптер'}){note}")
        print("  Если телефон не открывает ссылку, перезапустите с нужным адресом:")
        print(f"    python app.py --ip <адрес>")
    if any(s for _a, _n, s in found):
        print("  Совет: на время показа выключите VPN — он может мешать телефону достучаться.")
    if args.https:
        print("  HTTPS: на телефоне сначала будет предупреждение о подключении.")
        print("         Нажмите «Дополнительно» → «Перейти на сайт» (на iPhone: «Подробнее» →")
        print("         «посетить этот веб-сайт»). После этого запись голоса работает.")
    else:
        print("  Чтобы записывать голос с телефона, запускайте run_https.bat.")
    print(f"  ffmpeg: {mixer.FFMPEG}")
    print(f"  {ai.status_line()}")
    print("  Остановить: Ctrl+C")
    print("=" * 64)

    ssl_context = None
    if args.https:
        try:
            ssl_context = https_cert(dict.fromkeys([ip] + [a for a, _n, _s in found]))
        except ImportError:
            print("Для --https установите пакет: pip install cryptography")
            raise SystemExit(1)
    app.run(host="0.0.0.0", port=args.port, debug=False, threaded=True, ssl_context=ssl_context)


storage.init()
photos.register(app, THEMES)

if __name__ == "__main__":
    main()
