"""
Хранение данных в SQLite (один файл data/cards.db, создаётся автоматически).

Таблицы:
  uploads — загруженные голоса и музыка (файлы лежат в data/uploads/)
  cards   — готовые открытки (итоговый звук лежит в data/cards/)
"""
import os
import sqlite3
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
CARD_DIR = os.path.join(DATA_DIR, "cards")
PREVIEW_DIR = os.path.join(DATA_DIR, "previews")
DB_PATH = os.path.join(DATA_DIR, "cards.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('voice', 'music')),
    file         TEXT NOT NULL,          -- имя файла в data/uploads
    title        TEXT,                   -- для музыки: название, которое видит пользователь
    duration_ms  INTEGER NOT NULL,
    source       TEXT,                   -- 'record' (микрофон) или 'file' (загрузка)
    consent      INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    code          TEXT PRIMARY KEY,      -- код из ссылки /c/<code>
    theme         TEXT NOT NULL,
    recipient     TEXT,
    sender        TEXT,
    message       TEXT,
    voice_id      TEXT NOT NULL REFERENCES uploads(id),
    track_id      TEXT,                  -- 'lib:<id>' или 'up:<id>'
    voice_volume  INTEGER NOT NULL,
    music_volume  INTEGER NOT NULL,
    audio_file    TEXT NOT NULL,         -- имя файла в data/cards
    duration_ms   INTEGER NOT NULL,
    open_at       TEXT,                  -- UTC ISO; NULL = открыть сразу
    created_at    TEXT NOT NULL,
    opened_count  INTEGER NOT NULL DEFAULT 0
);
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init():
    for d in (DATA_DIR, UPLOAD_DIR, CARD_DIR, PREVIEW_DIR):
        os.makedirs(d, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


def add_upload(upload_id, kind, file, duration_ms, source, consent, title=None):
    with connect() as conn:
        conn.execute(
            "INSERT INTO uploads (id, kind, file, title, duration_ms, source, consent, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (upload_id, kind, file, title, duration_ms, source, int(consent), now_iso()),
        )


def get_upload(upload_id, kind):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM uploads WHERE id = ? AND kind = ?", (upload_id, kind)
        ).fetchone()


def add_card(**card):
    card.setdefault("created_at", now_iso())
    cols = ", ".join(card)
    marks = ", ".join("?" for _ in card)
    with connect() as conn:
        conn.execute(f"INSERT INTO cards ({cols}) VALUES ({marks})", tuple(card.values()))


def get_card(code):
    with connect() as conn:
        return conn.execute("SELECT * FROM cards WHERE code = ?", (code,)).fetchone()


def mark_opened(code):
    with connect() as conn:
        conn.execute("UPDATE cards SET opened_count = opened_count + 1 WHERE code = ?", (code,))
