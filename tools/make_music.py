"""
Генератор фоновой музыки для открыток.

Зачем: по правилам хакатона музыка должна быть своей или с разрешённой
лицензией. Этот скрипт синтезирует три коротких трека с нуля (numpy),
поэтому права на них принадлежат команде и вопросов на защите не будет.

Запуск (из корня проекта):
    python tools/make_music.py
Результат: music/*.mp3 (перезаписывает существующие файлы с теми же именами).
"""
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mixer import FFMPEG  # noqa: E402  — тот же ffmpeg, что использует сервер

SR = 44100
RNG = np.random.default_rng(7)
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "music")


# ---------- базовые «инструменты» ----------

def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def t_axis(dur):
    return np.arange(int(dur * SR)) / SR


def env_adsr(n, a=0.01, d=0.1, s=0.7, r=0.2):
    a_n, d_n, r_n = int(a * SR), int(d * SR), int(r * SR)
    s_n = max(n - a_n - d_n - r_n, 0)
    e = np.concatenate([
        np.linspace(0, 1, a_n, endpoint=False),
        np.linspace(1, s, d_n, endpoint=False),
        np.full(s_n, s),
        np.linspace(s, 0, r_n),
    ])
    return np.pad(e, (0, max(0, n - len(e))))[:n]


def piano(freq, dur):
    t = t_axis(dur)
    out = np.zeros_like(t)
    for k, amp in enumerate([1.0, 0.45, 0.22, 0.12, 0.06], start=1):
        out += amp * np.sin(2 * np.pi * freq * k * t) * np.exp(-t * (2.2 + 1.6 * k))
    out *= np.minimum(1, t / 0.004)
    return out * 0.5


def pluck(freq, dur):
    t = t_axis(dur)
    out = np.zeros_like(t)
    for k, amp in enumerate([1.0, 0.6, 0.35, 0.2, 0.1, 0.05], start=1):
        out += amp * np.sin(2 * np.pi * freq * k * t) * np.exp(-t * (5 + 3 * k))
    out *= np.minimum(1, t / 0.002)
    return out * 0.45


def bell(freq, dur):
    t = t_axis(dur)
    out = np.zeros_like(t)
    for ratio, amp, dec in [(1, 1.0, 2.5), (2.76, 0.4, 4), (5.4, 0.2, 7), (8.93, 0.08, 10)]:
        out += amp * np.sin(2 * np.pi * freq * ratio * t) * np.exp(-t * dec)
    out *= np.minimum(1, t / 0.002)
    return out * 0.35


def pad(freqs, dur):
    t = t_axis(dur)
    out = np.zeros_like(t)
    for f in freqs:
        for det in (-0.12, 0.0, 0.12):  # лёгкая расстройка — «живое» звучание
            ff = f * 2 ** (det / 12)
            for k in range(1, 6):
                out += (1 / k ** 1.6) * np.sin(2 * np.pi * ff * k * t + RNG.uniform(0, 6.28))
    out *= env_adsr(len(t), a=0.6, d=0.3, s=0.85, r=0.8)
    return out * 0.06 / max(1, len(freqs))


def bass(freq, dur):
    t = t_axis(dur)
    out = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(4 * np.pi * freq * t)
    return out * env_adsr(len(t), a=0.01, d=0.15, s=0.6, r=0.12) * 0.35


def kick():
    t = t_axis(0.35)
    f = 45 + 80 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9) * 0.8


def hat(dur=0.08, level=0.12):
    n = RNG.standard_normal(int(dur * SR))
    n = np.diff(n, prepend=0)  # грубый ФВЧ — оставляем «шипение»
    n = np.convolve(n, np.ones(3) / 3, mode="same")  # срезаем самые резкие верха
    return n * np.exp(-t_axis(dur) * 70) * level


def clap():
    t = t_axis(0.25)
    n = np.diff(RNG.standard_normal(len(t)), prepend=0)
    n = np.convolve(n, np.ones(4) / 4, mode="same")
    return (n * 0.35 + 0.3 * np.sin(2 * np.pi * 190 * t)) * np.exp(-t * 22) * 0.3


# ---------- микширование ----------

class Track:
    def __init__(self, seconds):
        self.buf = np.zeros((int(seconds * SR) + SR * 3, 2))

    def add(self, sig, at, pan=0.0, gain=1.0):
        i = int(at * SR)
        j = min(i + len(sig), len(self.buf))
        if i >= len(self.buf):
            return
        left = np.cos((pan + 1) * np.pi / 4)
        right = np.sin((pan + 1) * np.pi / 4)
        self.buf[i:j, 0] += sig[: j - i] * left * gain
        self.buf[i:j, 1] += sig[: j - i] * right * gain


def reverb(stereo, seconds=1.8, mix=0.22):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros_like(stereo)
    for ch in range(2):
        ir = RNG.standard_normal(n) * np.exp(-t * 3.2)
        ir = np.convolve(ir, np.ones(8) / 8, mode="same")  # смягчаем верха
        ir[0] = 0
        size = 1 << int(np.ceil(np.log2(len(stereo) + n)))
        wet = np.fft.irfft(np.fft.rfft(stereo[:, ch], size) * np.fft.rfft(ir, size), size)[: len(stereo)]
        wet /= np.max(np.abs(wet)) + 1e-9
        dry_peak = np.max(np.abs(stereo[:, ch])) + 1e-9
        out[:, ch] = stereo[:, ch] + wet * dry_peak * mix
    return out


def master(stereo, total_seconds):
    stereo = stereo[: int(total_seconds * SR)]
    stereo = np.tanh(stereo * 1.2) / np.tanh(1.2)  # мягкое ограничение пиков
    fade = int(1.5 * SR)
    stereo[:int(0.05 * SR)] *= np.linspace(0, 1, int(0.05 * SR))[:, None]
    stereo[-fade:] *= np.linspace(1, 0, fade)[:, None]
    return stereo / (np.max(np.abs(stereo)) + 1e-9) * 0.89


def save_mp3(stereo, name):
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2").tobytes()
    path = os.path.join(OUT_DIR, name)
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", "pipe:0",
         "-codec:a", "libmp3lame", "-b:a", "192k", path],
        input=pcm, check=True,
    )
    print("готово:", path)


# ---------- три трека ----------
# Аккорды: (корень MIDI, тип). Мажор = 0,4,7; минор = 0,3,7
MAJ, MIN = (0, 4, 7), (0, 3, 7)


def chord_notes(root, kind, octave_shift=0):
    return [root + i + 12 * octave_shift for i in kind]


def warm_evening():
    """Тёплый вечер — медленный, спокойный: пианино-арпеджио и пэд. Хорошо ложится под голос."""
    bpm = 76
    beat = 60 / bpm
    prog = [(53, MAJ), (48, MAJ), (50, MIN), (46, MAJ)]  # F – C – Dm – Bb
    bars = 16
    tr = Track(bars * 4 * beat)
    for b in range(bars):
        root, kind = prog[b % 4]
        start = b * 4 * beat
        tr.add(pad([hz(n) for n in chord_notes(root, kind, 1)], 4 * beat + 0.6), start, gain=1.0)
        tr.add(bass(hz(root - 12), 4 * beat * 0.95), start, gain=0.8)
        arp = chord_notes(root, kind, 1) + [root + 24] + chord_notes(root, kind, 1)[1:][::-1]
        for i in range(8):
            note = arp[i % len(arp)]
            tr.add(piano(hz(note), 1.6), start + i * beat / 2, pan=-0.3 + 0.6 * (i % 2), gain=0.55)
        if b % 2 == 1:
            tr.add(piano(hz(chord_notes(root, kind, 2)[2]), 2.2), start + 3.5 * beat, pan=0.2, gain=0.3)
    return master(reverb(tr.buf, 2.2, 0.3), bars * 4 * beat)


def celebration():
    """Праздник — бодрый: щипковые аккорды, бас восьмыми, лёгкие ударные."""
    bpm = 116
    beat = 60 / bpm
    prog = [(48, MAJ), (43, MAJ), (45, MIN), (41, MAJ)]  # C – G – Am – F
    bars = 24
    tr = Track(bars * 4 * beat)
    rhythm = [0, 0.75, 1.5, 2, 2.75, 3.5]  # синкопа
    for b in range(bars):
        root, kind = prog[b % 4]
        start = b * 4 * beat
        notes = chord_notes(root, kind, 2)
        for pos in rhythm:
            for k, n in enumerate(notes):
                tr.add(pluck(hz(n), 0.5), start + pos * beat + k * 0.004, pan=-0.25 + 0.25 * k, gain=0.5)
        tr.add(pad([hz(n) for n in chord_notes(root, kind, 1)], 4 * beat + 0.3), start, gain=0.7)
        for i in range(8):
            n = root - 12 + (12 if i % 2 else 0)
            tr.add(bass(hz(n), beat / 2 * 0.9), start + i * beat / 2, gain=0.7)
        if b >= 2:  # первые два такта — без барабанов, как вступление
            for i in range(4):
                if i in (0, 2):
                    tr.add(kick(), start + i * beat, gain=0.9)
                else:
                    tr.add(clap(), start + i * beat, pan=0.05, gain=0.8)
            for i in range(8):
                tr.add(hat(level=0.05 if i % 2 else 0.03), start + i * beat / 2, pan=0.35)
        # короткая мелодия-«колокольчик» каждые 4 такта
        if b % 4 == 3:
            for i, step in enumerate([7, 9, 12, 14]):
                tr.add(bell(hz(root + 24 + step), 1.2), start + (2 + i * 0.5) * beat, pan=0.3, gain=0.5)
    return master(reverb(tr.buf, 1.4, 0.18), bars * 4 * beat)


def snowflakes():
    """Снежинки — зимний, «музыкальная шкатулка» поверх мягкого пэда."""
    bpm = 92
    beat = 60 / bpm
    prog = [(45, MAJ), (42, MIN), (50, MAJ), (52, MAJ)]  # A – F#m – D – E
    bars = 20
    tr = Track(bars * 4 * beat)
    # мелодия на 4 такта (ступени мажорной гаммы от тоники A5), длительность в долях
    scale = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16]
    melody = [
        [(4, 1), (2, 0.5), (4, 0.5), (7, 1), (4, 1)],
        [(5, 1), (4, 0.5), (2, 0.5), (0, 2)],
        [(3, 1), (4, 0.5), (5, 0.5), (7, 1), (9, 1)],
        [(8, 1.5), (7, 0.5), (6, 1), (4, 1)],
    ]
    for b in range(bars):
        root, kind = prog[b % 4]
        start = b * 4 * beat
        tr.add(pad([hz(n) for n in chord_notes(root, kind, 1)], 4 * beat + 0.8), start, gain=1.1)
        tr.add(bass(hz(root - 12), 2 * beat), start, gain=0.5)
        tr.add(bass(hz(root - 12), 2 * beat), start + 2 * beat, gain=0.4)
        if b >= 2:
            pos = 0.0
            for deg, dur in melody[b % 4]:
                tr.add(bell(hz(69 + 12 + scale[deg]), 1.8), start + pos * beat, pan=-0.15, gain=0.7)
                pos += dur
        if b >= 4:
            for i in range(16):  # «бубенцы»
                tr.add(hat(0.05, level=0.025 if i % 4 else 0.04), start + i * beat / 4, pan=0.4)
    return master(reverb(tr.buf, 2.6, 0.35), bars * 4 * beat)


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    save_mp3(warm_evening(), "warm-evening.mp3")
    save_mp3(celebration(), "celebration.mp3")
    save_mp3(snowflakes(), "snowflakes.mp3")
