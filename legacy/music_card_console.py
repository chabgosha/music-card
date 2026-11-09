import os
os.environ["PATH"] += os.pathsep + r"C:\ffmpeg-2026-10-12-git-615b07170e-essentials_build\bin" # добавить свой путь или закинуть файл по этому пути
import uuid
import threading
import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write as wav_write
from pydub import AudioSegment
from flask import Flask, send_from_directory, render_template_string, abort

# ============ НАСТРОЙКИ ============
OUTPUT_DIR = "cards"
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__)


# ============ ЗАПИСЬ ГОЛОСА ============
def record_voice(duration=10, samplerate=44100):
    print(f"\n🎙  Запись {duration} сек. Говорите...")
    recording = sd.rec(int(duration * samplerate),
                       samplerate=samplerate,
                       channels=1,
                       dtype='int16')
    sd.wait()
    print("✅ Запись завершена.")
    path = "voice_raw.wav"
    wav_write(path, samplerate, recording)
    return path


# ============ ОБРАБОТКА ЗВУКА ============
def build_card(voice_path, music_path, voice_vol_db=0.0, music_vol_db=-10.0,
               output_path="card.mp3"):
    """Смешивает голос и музыку с настройкой громкости (дБ)."""
    if not os.path.exists(voice_path):
        raise FileNotFoundError(f"Нет файла голоса: {voice_path}")
    if not os.path.exists(music_path):
        raise FileNotFoundError(f"Нет файла музыки: {music_path}")

    voice = AudioSegment.from_file(voice_path)
    music = AudioSegment.from_file(music_path)

    if len(voice) == 0:
        raise ValueError("Голосовой файл пустой")
    if len(music) == 0:
        raise ValueError("Музыкальный файл пустой")

    # Громкость
    voice = voice.apply_gain(voice_vol_db)
    music = music.apply_gain(music_vol_db)

    # Зацикливаем музыку (экономно: используем list и sum)
    if len(music) < len(voice):
        loops = len(voice) // len(music) + 1
        # Ограничим, чтобы не сожрать память: максимум 200 повторов
        loops = min(loops, 200)
        music = music * loops

    music = music[:len(voice)]

    # Плавное затухание — но не длиннее самой музыки
    fade_ms = min(2000, len(music) // 2)
    if fade_ms > 0:
        music = music.fade_out(fade_ms)

    mixed = voice.overlay(music)
    mixed.export(output_path, format="mp3")
    print(f"💾 Готово: {output_path}")
    return output_path


# ============ ПРОСЛУШИВАНИЕ ============
def preview(path):
    """Корректное проигрывание: конвертируем в float32 [-1,1]."""
    print(f"\n▶  Прослушивание: {path}")
    audio = AudioSegment.from_file(path)
    samples = np.array(audio.get_array_of_samples(), dtype=np.float32)

    # Нормализация int16 → [-1, 1]
    max_val = float(1 << (8 * audio.sample_width - 1))
    samples = samples / max_val

    # Решейп для стерео
    if audio.channels == 2:
        samples = samples.reshape((-1, 2))

    sd.play(samples, audio.frame_rate)
    sd.wait()


# ============ ВЕБ-СЕРВЕР ============
PAGE_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <title>🎵 Музыкальная открытка</title>
    <style>
        body { font-family: sans-serif; background: linear-gradient(135deg,#667eea,#764ba2);
               color:#fff; display:flex; flex-direction:column; align-items:center;
               justify-content:center; height:100vh; margin:0; }
        .card { background: rgba(255,255,255,.15); padding: 40px; border-radius: 20px;
                text-align:center; backdrop-filter: blur(10px); }
        h1 { margin: 0 0 20px; }
        audio { width: 320px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🎵 Вам музыкальная открытка!</h1>
        <p>Нажмите ▶ чтобы прослушать поздравление</p>
        <audio controls autoplay src="/audio/{{ filename }}"></audio>
    </div>
</body>
</html>
"""


@app.route("/card/<filename>")
def card_page(filename):
    if not os.path.exists(os.path.join(OUTPUT_DIR, filename)):
        abort(404)
    return render_template_string(PAGE_TEMPLATE, filename=filename)


@app.route("/audio/<filename>")
def serve_audio(filename):
    return send_from_directory(OUTPUT_DIR, filename)


def start_server(port=5000):
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def publish(filename, port=5000):
    t = threading.Thread(target=start_server, args=(port,), daemon=True)
    t.start()
    return f"http://localhost:{port}/card/{filename}"


# ============ ИНТЕРАКТИВНЫЙ СЦЕНАРИЙ ============
def main():
    print("=" * 50)
    print("🎵  МУЗЫКАЛЬНАЯ ОТКРЫТКА")
    print("=" * 50)

    print("\n[1] Голосовое поздравление:")
    print("    1 — записать с микрофона")
    print("    2 — использовать готовый файл")
    choice = input("Выбор (1/2): ").strip()

    if choice == "1":
        dur = int(input("Длительность записи (сек, по умолч. 10): ") or "10")
        voice_path = record_voice(duration=dur)
    else:
        voice_path = input("Путь к файлу голоса: ").strip().strip('"')

    print("\n[2] Фоновая музыка:")
    music_path = input("Путь к музыкальному файлу: ").strip().strip('"')

    print("\n[3] Настройка громкости (дБ, 0 = оригинал, -10 = тише):")
    voice_vol = float(input("  Громкость голоса (по умолч. 0): ") or "0")
    music_vol = float(input("  Громкость музыки (по умолч. -10): ") or "-10")

    filename = f"card_{uuid.uuid4().hex[:8]}.mp3"
    output = os.path.join(OUTPUT_DIR, filename)
    build_card(voice_path, music_path, voice_vol, music_vol, output)

    while True:
        preview(output)
        ans = input("\nВсё устраивает? (y — да / n — изменить громкость): ").strip().lower()
        if ans == "y":
            break
        voice_vol = float(input("  Новая громкость голоса: ") or str(voice_vol))
        music_vol = float(input("  Новая громкость музыки: ") or str(music_vol))
        build_card(voice_path, music_path, voice_vol, music_vol, output)

    port = int(input("\n[4] Порт для ссылки (по умолч. 5000): ") or "5000")
    url = publish(filename, port=port)
    print("\n" + "=" * 50)
    print(f"🔗 Ссылка для получателя:\n   {url}")
    print("=" * 50)

    input("\nНажмите Enter, чтобы закрыть сервер...\n")


if __name__ == "__main__":
    main()