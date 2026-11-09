"""
Фотографии в открытке (отдельный модуль, подключается в app.py одной строкой).

  • Отправитель может добавить фото — оно будет на лицевой стороне открытки, как полароид.
  • Получатель может «Ответить улыбкой»: снять селфи системной камерой телефона и отправить.
  • Отправитель видит на странице статуса /s/<token>, когда открытку открыли, и ответы-селфи.

Фото уменьшается и пересохраняется в JPEG прямо в браузере (static/js/photo-utils.js), поэтому
на сервер не попадают EXIF-метаданные (геолокация, модель телефона). Сервер дополнительно
проверяет, что это действительно картинка.

Страницы:
  /s/<token>                 — статус открытки для отправителя (ссылка секретная, её видит только он)
API (JSON):
  POST /api/photos            — загрузить фото для открытки (multipart: file, consent=1)
  POST /c/<code>/seen         — получатель открыл конверт (запоминаем время)
  POST /c/<code>/replies      — ответ-селфи получателя (multipart: file, consent=1)
  GET  /api/status/<token>    — то же, что страница статуса, для автообновления
Файлы:
  GET  /c/<code>/photo        — фото на открытке
  GET  /s/<token>/reply/<id>  — ответ-селфи (только по ссылке статуса)
"""
import os
import secrets
import sqlite3
from datetime import datetime, timezone

from flask import (Blueprint, abort, jsonify, render_template, request,
                   send_from_directory, url_for)

import storage

PHOTO_DIR = os.path.join(storage.DATA_DIR, "photos")
MAX_PHOTO_MB = 12
MAX_REPLIES = 20  # на одну открытку — чтобы ссылку нельзя было использовать как бесплатное хранилище

bp = Blueprint("photos", __name__)
THEMES = {}  # оформление поводов, передаётся из app.py в register()

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('card', 'reply')),
    file        TEXT NOT NULL,          -- имя файла в data/photos
    card_code   TEXT,                   -- для ответа сразу, для фото открытки — после создания
    consent     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

-- дополнительные сведения об открытке (таблицу cards не трогаем)
CREATE TABLE IF NOT EXISTS card_extras (
    code          TEXT PRIMARY KEY,     -- cards.code
    status_token  TEXT NOT NULL UNIQUE, -- секрет для страницы статуса /s/<token>
    photo_id      TEXT,                 -- фото на лицевой стороне (photos.id)
    first_seen_at TEXT,                 -- когда получатель впервые открыл конверт
    last_seen_at  TEXT,
    seen_count    INTEGER NOT NULL DEFAULT 0
);
"""


class PhotoError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


@bp.app_errorhandler(PhotoError)
def _photo_error(err):
    return jsonify(error=err.message), err.status


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def db():
    conn = sqlite3.connect(storage.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    os.makedirs(PHOTO_DIR, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)


def new_id(nbytes=6):
    return secrets.token_urlsafe(nbytes).replace("-", "x").replace("_", "z")


def image_ext(head):
    """Расширение по первым байтам файла или None, если это не JPEG/PNG/WebP."""
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def save_photo(kind, card_code=None):
    """Проверяет и сохраняет фото из request.files['file']. Возвращает id."""
    if request.form.get("consent") != "1":
        raise PhotoError("Подтвердите согласие, чтобы отправить фото.")
    file = request.files.get("file")
    if not file:
        raise PhotoError("Фото не получено.")
    data = file.read(MAX_PHOTO_MB * 1024 * 1024 + 1)
    if len(data) > MAX_PHOTO_MB * 1024 * 1024:
        raise PhotoError(f"Фото больше {MAX_PHOTO_MB} МБ.", 413)
    return store_photo(kind, data, card_code)


def store_photo(kind, data, card_code=None):
    """Сохраняет картинку (байты) в data/photos и в базу. Используется и для картинок от ИИ."""
    ext = image_ext(data[:16])
    if not ext:
        raise PhotoError("Этот файл не похож на фото. Подойдёт JPG, PNG или WebP.")
    photo_id = new_id()
    name = f"{kind}-{photo_id}{ext}"
    with open(os.path.join(PHOTO_DIR, name), "wb") as f:
        f.write(data)
    with db() as conn:
        conn.execute(
            "INSERT INTO photos (id, kind, file, card_code, consent, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (photo_id, kind, name, card_code, now_iso()),
        )
    return photo_id


def get_extras(code):
    with db() as conn:
        return conn.execute("SELECT * FROM card_extras WHERE code = ?", (code,)).fetchone()


def card_photo_url(code):
    """Адрес фото на лицевой стороне или None (используется в шаблоне открытки)."""
    extras = get_extras(code)
    if extras and extras["photo_id"]:
        return url_for("photos.card_photo", code=code)
    return None


def card_is_locked(card):
    return bool(card["open_at"]) and datetime.fromisoformat(card["open_at"]) > datetime.now(timezone.utc)


# ---------- вызывается из app.py после создания открытки ----------

def on_card_created(code, photo_id):
    """Запоминает фото открытки и выдаёт секретную ссылку на статус. Возвращает её путь."""
    token = new_id(9)
    photo = None
    if photo_id:
        with db() as conn:
            photo = conn.execute(
                "SELECT id FROM photos WHERE id = ? AND kind = 'card' AND card_code IS NULL",
                (str(photo_id),),
            ).fetchone()
            if photo:
                conn.execute("UPDATE photos SET card_code = ? WHERE id = ?", (code, photo["id"]))
    with db() as conn:
        conn.execute(
            "INSERT INTO card_extras (code, status_token, photo_id) VALUES (?, ?, ?)",
            (code, token, photo["id"] if photo else None),
        )
    return url_for("photos.status_page", token=token)


# ---------- отправитель ----------

@bp.post("/api/photos")
def api_upload_photo():
    photo_id = save_photo("card")
    return jsonify(id=photo_id, url=url_for("photos.draft_photo", photo_id=photo_id))


@bp.get("/photos/draft/<photo_id>")
def draft_photo(photo_id):
    """Фото, которое ещё не прикреплено к открытке (для предпросмотра в конструкторе)."""
    with db() as conn:
        row = conn.execute(
            "SELECT file FROM photos WHERE id = ? AND kind = 'card' AND card_code IS NULL", (photo_id,)
        ).fetchone()
    if not row:
        abort(404)
    return send_from_directory(PHOTO_DIR, row["file"])


@bp.get("/c/<code>/photo")
def card_photo(code):
    card = storage.get_card(code)
    extras = get_extras(code)
    if not card or not extras or not extras["photo_id"]:
        abort(404)
    if card_is_locked(card):
        abort(403)
    with db() as conn:
        row = conn.execute("SELECT file FROM photos WHERE id = ?", (extras["photo_id"],)).fetchone()
    if not row:
        abort(404)
    return send_from_directory(PHOTO_DIR, row["file"], conditional=True)


def status_data(token):
    with db() as conn:
        extras = conn.execute("SELECT * FROM card_extras WHERE status_token = ?", (token,)).fetchone()
        if not extras:
            return None
        replies = conn.execute(
            "SELECT id, created_at FROM photos WHERE kind = 'reply' AND card_code = ? ORDER BY created_at DESC",
            (extras["code"],),
        ).fetchall()
    card = storage.get_card(extras["code"])
    if not card:
        return None
    return {
        "code": card["code"],
        "recipient": card["recipient"],
        "sender": card["sender"],
        "theme": card["theme"],
        "open_at": card["open_at"],
        "created_at": card["created_at"],
        "listens": card["opened_count"],
        "first_seen_at": extras["first_seen_at"],
        "last_seen_at": extras["last_seen_at"],
        "seen_count": extras["seen_count"],
        "replies": [{"id": r["id"], "created_at": r["created_at"],
                     "url": url_for("photos.reply_photo", token=token, photo_id=r["id"])}
                    for r in replies],
    }


@bp.get("/s/<token>")
def status_page(token):
    data = status_data(token)
    if not data:
        abort(404)
    theme = THEMES.get(data["theme"]) or next(iter(THEMES.values()))
    return render_template("status.html", s=data, theme=theme, token=token,
                           card_url=url_for("card_page", code=data["code"]))


@bp.get("/api/status/<token>")
def api_status(token):
    data = status_data(token)
    if not data:
        abort(404)
    return jsonify(data)


@bp.get("/s/<token>/reply/<photo_id>")
def reply_photo(token, photo_id):
    with db() as conn:
        extras = conn.execute("SELECT code FROM card_extras WHERE status_token = ?", (token,)).fetchone()
        row = extras and conn.execute(
            "SELECT file FROM photos WHERE id = ? AND kind = 'reply' AND card_code = ?",
            (photo_id, extras["code"]),
        ).fetchone()
    if not row:
        abort(404)
    return send_from_directory(PHOTO_DIR, row["file"])


# ---------- получатель ----------

@bp.post("/c/<code>/seen")
def card_seen(code):
    card = storage.get_card(code)
    if not card or card_is_locked(card):
        abort(404)
    now = now_iso()
    with db() as conn:
        conn.execute(
            "UPDATE card_extras SET seen_count = seen_count + 1, last_seen_at = ?,"
            " first_seen_at = COALESCE(first_seen_at, ?) WHERE code = ?",
            (now, now, code),
        )
    return "", 204


@bp.post("/c/<code>/replies")
def card_reply(code):
    card = storage.get_card(code)
    if not card or not get_extras(code):
        raise PhotoError("Открытка не найдена.", 404)
    if card_is_locked(card):
        raise PhotoError("Открытка ещё не открылась.", 403)
    with db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM photos WHERE kind = 'reply' AND card_code = ?", (code,)
        ).fetchone()[0]
    if count >= MAX_REPLIES:
        raise PhotoError("На эту открытку уже отправлено много ответов.", 429)
    save_photo("reply", card_code=code)
    return jsonify(ok=True), 201


def register(app, themes):
    THEMES.update(themes)
    init()
    app.register_blueprint(bp)
    app.jinja_env.globals["card_photo_url"] = card_photo_url
    app.jinja_env.globals["photo_replies_enabled"] = lambda code: get_extras(code) is not None
