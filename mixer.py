"""
Сведение голоса и музыки в один файл открытки.

Основа — функция build_card из первой версии проекта (pydub: громкость,
зацикливание музыки, затухание). Что добавлено:
  * ffmpeg находится сам (из PATH или из пакета imageio-ffmpeg) — без ручных путей;
  * любые входные форматы (webm/ogg из браузера, m4a с iPhone, mp3, wav)
    декодируются через ffmpeg напрямую, ffprobe не нужен;
  * голос выравнивается по громкости и обрезается от тишины по краям;
  * громкость задаётся в процентах (как ползунок), внутри переводится в дБ;
  * вступление музыки перед голосом и «хвост» после него;
  * музыка приглушается, пока звучит голос (ducking), и возвращается после.
"""
import math
import os
import shutil
import subprocess
import warnings

# pydub при импорте ищет ffmpeg в PATH и пишет предупреждение, если не нашёл.
# Нам это не важно: путь к ffmpeg мы задаём сами ниже.
warnings.filterwarnings("ignore", message="Couldn't find ffmpeg", category=RuntimeWarning)

from pydub import AudioSegment  # noqa: E402
from pydub.silence import detect_leading_silence  # noqa: E402

SAMPLE_RATE = 44100

INTRO_MS = 2000          # музыка до начала голоса
OUTRO_MS = 3000          # музыка после голоса
DUCK_DB = 7.0            # насколько музыка громче во вступлении/концовке, чем под голосом
TARGET_DBFS = -18.0      # средняя громкость, к которой приводим голос и музыку
MAX_VOICE_MS = 3 * 60 * 1000
MAX_MUSIC_SECONDS = 4 * 60


class AudioError(Exception):
    """Понятная пользователю ошибка обработки звука."""


def _find_ffmpeg():
    env = os.environ.get("FFMPEG_BINARY")
    if env and os.path.exists(env):
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Не найден ffmpeg. Выполните: pip install imageio-ffmpeg"
        ) from exc


FFMPEG = _find_ffmpeg()
AudioSegment.converter = FFMPEG


# ---------- чтение и запись ----------

def decode(path, mono=False, max_seconds=None, voice_filter=False):
    """Любой аудиофайл → AudioSegment (16 бит, 44.1 кГц)."""
    channels = 1 if mono else 2
    cmd = [FFMPEG, "-v", "error", "-nostdin", "-i", path, "-vn"]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    if voice_filter:
        cmd += ["-af", "highpass=f=80"]  # убираем гул и низкочастотный шум
    cmd += ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(channels),
            "-ar", str(SAMPLE_RATE), "pipe:1"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise AudioError("Не получилось прочитать аудиофайл. Попробуйте mp3, wav или m4a.")
    return AudioSegment(data=proc.stdout, sample_width=2,
                        frame_rate=SAMPLE_RATE, channels=channels)


def encode_mp3(seg, out_path):
    """AudioSegment → mp3. Если в ffmpeg нет mp3-кодека, сохраняем m4a (AAC)."""
    seg = seg.set_frame_rate(SAMPLE_RATE).set_sample_width(2)
    base = [FFMPEG, "-y", "-v", "error", "-nostdin", "-f", "s16le",
            "-ar", str(SAMPLE_RATE), "-ac", str(seg.channels), "-i", "pipe:0"]
    proc = subprocess.run(base + ["-codec:a", "libmp3lame", "-b:a", "160k", out_path],
                          input=seg.raw_data, capture_output=True)
    if proc.returncode == 0:
        return out_path
    alt = os.path.splitext(out_path)[0] + ".m4a"
    proc = subprocess.run(base + ["-codec:a", "aac", "-b:a", "160k", alt],
                          input=seg.raw_data, capture_output=True)
    if proc.returncode != 0:
        raise AudioError("Не получилось сохранить итоговый файл.")
    return alt


# ---------- подготовка ----------

def percent_to_db(percent):
    """Ползунок 0–100 % → изменение громкости в дБ (100 % = 0 дБ, 0 % = тишина)."""
    p = max(0.0, min(100.0, float(percent)))
    return -120.0 if p == 0 else 20 * math.log10(p / 100)


def match_loudness(seg, target=TARGET_DBFS, max_boost=30.0):
    if seg.dBFS == float("-inf"):
        return seg
    return seg.apply_gain(max(-40.0, min(max_boost, target - seg.dBFS)))


def prepare_voice(path):
    """Голос: декодируем, обрезаем тишину по краям, выравниваем громкость."""
    voice = decode(path, mono=True, max_seconds=MAX_VOICE_MS / 1000 + 5, voice_filter=True)
    if len(voice) < 500 or voice.dBFS == float("-inf") or voice.dBFS < -60:
        raise AudioError("Запись слишком короткая или в ней тишина. Проверьте микрофон.")
    threshold = voice.dBFS - 18
    start = max(0, detect_leading_silence(voice, silence_threshold=threshold) - 150)
    end = max(0, detect_leading_silence(voice.reverse(), silence_threshold=threshold) - 250)
    trimmed = voice[start:len(voice) - end]
    if len(trimmed) >= 500:
        voice = trimmed
    if len(voice) > MAX_VOICE_MS:
        raise AudioError("Запись длиннее 3 минут. Сделайте поздравление короче.")
    return match_loudness(voice)


def prepare_music(path):
    music = decode(path, mono=False, max_seconds=MAX_MUSIC_SECONDS)
    if len(music) < 1000 or music.dBFS == float("-inf"):
        raise AudioError("В музыкальном файле нет звука.")
    return match_loudness(music)


def loop_to(music, length_ms, crossfade_ms=300):
    """Зацикливаем музыку до нужной длины, стыки сглаживаем."""
    out = music
    while len(out) < length_ms:
        cf = min(crossfade_ms, len(music) // 4, len(out) // 4)
        out = out.append(music, crossfade=cf)
        if len(out) > length_ms + len(music) * 200:  # защита от бесконечного цикла
            break
    return out[:length_ms]


# ---------- основное: сведение ----------

def build_card(voice, music, voice_percent=100, music_percent=35, natural_end=False):
    """
    voice, music — подготовленные AudioSegment (prepare_voice / prepare_music).
    Возвращает итоговый AudioSegment:
      [вступление музыки] → [голос поверх приглушённой музыки] → [музыка и затухание]
    natural_end=True — музыка уже сочинена под длину голоса и сама заканчивается
    финальным аккордом (composer.py): не зацикливаем и не обрезаем её затуханием.
    """
    voice = voice.apply_gain(percent_to_db(voice_percent))
    bed_db = percent_to_db(music_percent)
    total = INTRO_MS + len(voice) + OUTRO_MS
    if natural_end and music is not None:
        total = max(total, len(music))

    if music is None or bed_db <= -100:
        silence = AudioSegment.silent(duration=total, frame_rate=SAMPLE_RATE).set_channels(2)
        mixed = silence.overlay(voice, position=INTRO_MS)
    else:
        bed = loop_to(music, total).apply_gain(bed_db)
        lift = min(DUCK_DB, -bed_db) if bed_db < 0 else 0.0  # громче, но не громче голоса
        voice_end = INTRO_MS + len(voice)
        if lift > 0.5:
            # pydub.fade: до start — from_gain, после end — to_gain, между — плавный переход
            bed = bed.fade(from_gain=lift, to_gain=0, start=INTRO_MS - 500, end=INTRO_MS + 200)
            bed = bed.fade(from_gain=0, to_gain=lift, start=voice_end - 100, end=voice_end + 700)
        bed = bed.fade_in(700).fade_out(200 if natural_end else min(2500, OUTRO_MS))
        mixed = bed.overlay(voice, position=INTRO_MS)

    # Защита от перегруза: пик не выше −1 dBFS
    if mixed.max_dBFS > -1.0:
        mixed = mixed.apply_gain(-1.0 - mixed.max_dBFS)
    return mixed
