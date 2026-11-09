"""
Генератор своей мелодии по настроению («Создать свою мелодию»).

Работает так же, как tools/make_music.py (те же синтезированные инструменты на numpy,
без интернета и без чужих сэмплов), но мелодия сочиняется заново:
  * настроение задаёт правила: лад, темп, размер, аккорды, инструменты, ударные;
  * число seed выбирает тональность, гармонию, ритм и рисунок мелодии —
    кнопка «Ещё вариант» просто берёт другой seed;
  * мелодия строится фразами «мотив — повтор — ответ — повтор», ударные доли
    попадают в звуки аккорда, поэтому она звучит осмысленно, а не случайно;
  * длина подгоняется под запись голоса, а в конце звучит каденция V → I
    и финальный аккорд (вместо зацикливания и затухания);
  * по желанию мотив строится из букв имени получателя.

Одинаковые (настроение, seed, имя, длина) всегда дают одинаковую мелодию,
поэтому готовые файлы можно кэшировать и при необходимости пересоздать.
"""
import functools
import hashlib
import math
import os
import subprocess
from collections import OrderedDict

import numpy as np

from mixer import FFMPEG
from tools import make_music as synth
from tools.make_music import SR, Track, bass, bell, clap, env_adsr, hat, hz, kick, pad, piano, pluck, t_axis

# ---------- лады ----------
MAJOR = (0, 2, 4, 5, 7, 9, 11)
MINOR = (0, 2, 3, 5, 7, 8, 10)
LYDIAN = (0, 2, 4, 6, 7, 9, 11)   # мажор с повышенной IV — «волшебное» звучание


# ---------- дополнительные инструменты ----------

def brass(freq, dur):
    """Мягкие «фанфары»: пилообразный спектр с плавной атакой и лёгким вибрато."""
    t = t_axis(dur)
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * t) * np.minimum(1, t / 0.3)
    phase = 2 * np.pi * freq * np.cumsum(vib) / SR
    out = np.zeros_like(t)
    for k in range(1, 9):
        out += np.sin(k * phase) / k * (1.0 if k < 4 else 0.6)
    return out * env_adsr(len(t), a=0.04, d=0.12, s=0.75, r=min(0.15, dur / 3)) * 0.22


def strings(freq, dur):
    """Струнные: медленная атака, вибрато, три слегка расстроенных голоса."""
    t = t_axis(dur)
    out = np.zeros_like(t)
    vib = 1 + 0.003 * np.sin(2 * np.pi * 5 * t)
    for det in (-0.08, 0.0, 0.08):
        phase = 2 * np.pi * freq * 2 ** (det / 12) * np.cumsum(vib) / SR
        for k in range(1, 6):
            out += np.sin(k * phase) / k ** 1.3
    return out * env_adsr(len(t), a=min(0.25, dur / 3), d=0.1, s=0.8, r=min(0.4, dur / 3)) * 0.07


def music_box(freq, dur):
    """Музыкальная шкатулка: колокольчик с быстрым затуханием и «металлическим» обертоном."""
    t = t_axis(dur)
    out = (np.sin(2 * np.pi * freq * t) * np.exp(-t * 3.5)
           + 0.35 * np.sin(2 * np.pi * freq * 4.07 * t) * np.exp(-t * 9)
           + 0.12 * np.sin(2 * np.pi * freq * 10.2 * t) * np.exp(-t * 16))
    return out * np.minimum(1, t / 0.0015) * 0.32


def crash(dur=2.5, level=0.08):
    n = np.diff(synth.RNG.standard_normal(int(dur * SR)), prepend=0)
    return n * np.exp(-t_axis(dur) * 2.2) * level


def shaker(level=0.02):
    return hat(0.05, level=level)


def _cached(fn):
    """Один и тот же звук (нота, длительность) синтезируем один раз — генерация в разы быстрее."""
    @functools.lru_cache(maxsize=512)
    def by_key(*key):
        out = fn(*key)
        out.flags.writeable = False
        return out

    @functools.wraps(fn)
    def wrapper(*args):
        return by_key(*(round(a, 4) if isinstance(a, float) else (tuple(round(x, 3) for x in a) if isinstance(a, list) else a) for a in args))
    return wrapper


piano, pluck, bell, bass, brass, strings, music_box = map(_cached, (piano, pluck, bell, bass, brass, strings, music_box))
pad = _cached(lambda freqs, dur: synth.pad(list(freqs), dur))
kick = functools.lru_cache(maxsize=1)(kick)

INSTRUMENTS = {"piano": piano, "pluck": pluck, "bell": bell, "brass": brass,
               "strings": strings, "music_box": music_box}

# ---------- настроения ----------
# progs — ступени лада (0 = тоника), keys — тоника в MIDI (около C4..A4),
# rhythms — ритм мелодии на такт в долях, lead — инструмент мелодии и его октава.
MOODS = OrderedDict([
    ("happy", {
        "title": "Весёлая", "emoji": "😄", "about": "мажор, бодрый темп, ударные",
        "scale": MAJOR, "bpm": (120, 132), "meter": 4, "keys": (60, 62, 65, 67),
        "progs": [(0, 4, 5, 3), (0, 3, 4, 0), (0, 5, 3, 4), (3, 4, 0, 5)],
        "rhythms": [(1, .5, .5, 1, 1), (.5, .5, .5, .5, 1, 1), (1.5, .5, 1, 1), (.5, 1, .5, 2), (1, 1, .5, .5, 1)],
        "lead": ("pluck", 12), "lead2": ("bell", 24, 0.25), "rest": 0.08,
        "reverb": (1.3, 0.16),
    }),
    ("tender", {
        "title": "Нежная", "emoji": "🌙", "about": "минор, медленно, пианино",
        "scale": MINOR, "bpm": (64, 72), "meter": 4, "keys": (57, 60, 62, 64),
        "progs": [(0, 5, 2, 6), (0, 5, 3, 4), (0, 3, 6, 2), (5, 6, 0, 0)],
        "rhythms": [(2, 2), (1, 1, 2), (3, 1), (1.5, .5, 2), (1, 2, 1)],
        "lead": ("piano", 12), "lead2": ("strings", 0, 0.5), "rest": 0.05,
        "reverb": (2.6, 0.34),
    }),
    ("festive", {
        "title": "Праздничная", "emoji": "🎉", "about": "фанфары, колокольчики, бочка",
        "scale": MAJOR, "bpm": (108, 116), "meter": 4, "keys": (58, 60, 62, 65),
        "progs": [(0, 3, 4, 0), (0, 5, 3, 4), (0, 3, 0, 4), (3, 4, 2, 5)],
        "rhythms": [(1, 1, 1, 1), (.75, .25, 1, 2), (1, .5, .5, 2), (.5, .5, 1, 1, 1), (1.5, .5, 2)],
        "lead": ("brass", 0), "lead2": ("bell", 24, 0.3), "rest": 0.04,
        "reverb": (1.8, 0.22),
    }),
    ("fairy", {
        "title": "Сказочная", "emoji": "✨", "about": "музыкальная шкатулка, волшебный лад",
        "scale": LYDIAN, "bpm": (84, 94), "meter": 4, "keys": (62, 64, 65, 67),
        "progs": [(0, 1, 0, 1), (0, 1, 5, 4), (0, 5, 1, 4), (0, 2, 1, 0)],
        "rhythms": [(1, 1, 1, 1), (.5, .5, 1, 2), (1, .5, .5, 1, 1), (2, 1, 1), (.5, .5, .5, .5, 2)],
        "lead": ("music_box", 24), "lead2": None, "rest": 0.06,
        "reverb": (3.0, 0.4),
    }),
    ("romantic", {
        "title": "Романтичная", "emoji": "💞", "about": "вальс, пианино и струнные",
        "scale": MAJOR, "bpm": (132, 150), "meter": 3, "keys": (60, 63, 65, 67),
        "progs": [(0, 5, 1, 4), (0, 2, 3, 4), (3, 4, 2, 5), (0, 3, 1, 4)],
        "rhythms": [(2, 1), (1, 1, 1), (1.5, .5, 1), (3,), (1, 2)],
        "lead": ("piano", 12), "lead2": ("strings", 12, 0.45), "rest": 0.04,
        "reverb": (2.4, 0.3), "sevenths": True,
    }),
])

# повод открытки → настроение по умолчанию
THEME_MOOD = {"birthday": "happy", "newyear": "fairy", "thanks": "tender",
              "congrats": "festive", "love": "romantic", "custom": "happy"}

RU_ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"


# ---------- теория ----------

def degree_pitch(tonic, scale, deg):
    """Ступень (может быть больше 6 или отрицательной) → MIDI-нота."""
    octave, idx = divmod(deg, len(scale))
    return tonic + scale[idx] + 12 * octave


def chord_degrees(root_deg, seventh=False):
    degs = [root_deg, root_deg + 2, root_deg + 4]
    if seventh:
        degs.append(root_deg + 6)
    return degs


def nearest_chord_tone(deg, chord):
    """Ближайшая к deg ступень, которая входит в аккорд (в любой октаве)."""
    best = None
    for c in chord:
        for octv in range(-2, 4):
            cand = c % 7 + 7 * octv
            if best is None or abs(cand - deg) < abs(best - deg):
                best = cand
    return best


def name_degrees(name):
    """Буквы имени → ступени лада (а=0, б=1, …), не больше 8 звуков."""
    out = []
    for ch in (name or "").lower():
        if ch in RU_ALPHABET:
            out.append(RU_ALPHABET.index(ch) % 8)
        elif "a" <= ch <= "z":
            out.append((ord(ch) - 97) % 8)
    return out[:8]


# ---------- сочинение ----------

def bar_count(mood, target_seconds, bpm):
    """Сколько тактов нужно, чтобы финальный аккорд прозвучал после target_seconds."""
    bar = MOODS[mood]["meter"] * 60 / bpm
    body = max(2, math.ceil(target_seconds / bar))
    return body + 1  # + такт финального аккорда


def plan(mood, seed, name=""):
    """Выбор всех «случайных» параметров по seed: тональность, темп, гармония, мотивы."""
    m = MOODS[mood]
    salt = hashlib.sha1(f"{mood}|{seed}|{name}".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(salt[:8], "little"))
    plan_ = {
        "rng": rng,
        "tonic": int(rng.choice(m["keys"])),
        "bpm": int(rng.integers(m["bpm"][0], m["bpm"][1] + 1)),
        "prog_a": m["progs"][int(rng.integers(len(m["progs"])))],
        "prog_b": m["progs"][int(rng.integers(len(m["progs"])))],
    }

    def motif(bars=2):
        """Мотив: ритм + «контур» (шаги по ладу)."""
        notes = []
        for _ in range(bars):
            rhythm = m["rhythms"][int(rng.integers(len(m["rhythms"])))]
            for d in rhythm:
                step = int(rng.choice([-2, -1, -1, 0, 1, 1, 2, 3, -3]))
                rest = rng.random() < m["rest"]
                notes.append([d, step, rest])
        return notes

    letters = name_degrees(name)
    if letters:
        # мотив из имени: каждая буква — звук, ровными долями на два такта
        total = 2 * m["meter"]
        dur = max(0.5, min(1.0, total / len(letters)))
        notes, used = [], 0.0
        prev = letters[0]
        for i, deg in enumerate(letters):
            d = dur if i < len(letters) - 1 else total - used
            notes.append([d, deg - prev, False])
            used += dur
            prev = deg
        plan_["motif_a"] = notes
        plan_["name_start"] = letters[0]
    else:
        plan_["motif_a"] = motif()
    plan_["motif_b"] = motif()
    return plan_


def compose(mood, seed, bars, name=""):
    """Возвращает стерео-numpy и длительность в секундах."""
    if mood not in MOODS:
        raise ValueError("unknown mood")
    m = MOODS[mood]
    p = plan(mood, seed, name)
    rng = p["rng"]
    tonic, scale, meter = p["tonic"], m["scale"], m["meter"]
    beat = 60 / p["bpm"]
    bar_s = meter * beat
    final_bar = bars - 1
    ring = 3.2
    total = final_bar * bar_s + ring
    tr = Track(total + 1)
    sevenths = m.get("sevenths", False)

    # гармония по тактам: A-прогрессия, B-прогрессия, …; перед финалом — V (доминанта)
    chords = []
    for b in range(final_bar):
        prog = p["prog_a"] if (b // 4) % 2 == 0 else p["prog_b"]
        chords.append(prog[b % 4])
    if final_bar >= 2:
        chords[-1] = 4
    chords.append(0)

    def chord_midi(deg, octave=0):
        return [degree_pitch(tonic, scale, d) + 12 * octave for d in chord_degrees(deg, sevenths)]

    # ---------- мелодия ----------
    lead_name, lead_oct = m["lead"][0], m["lead"][1]
    lead = INSTRUMENTS[lead_name]
    lead2 = m["lead2"]
    intro_bars = 1 if bars > 5 else 0
    cur = p.get("name_start", 4)  # начинаем с квинты лада — так мелодия «поднимается» к тонике

    def play_note(deg, at, dur, gain=0.62):
        midi = degree_pitch(tonic, scale, deg) + lead_oct
        length = dur * beat
        sustain = {"piano": length + 1.2, "pluck": 0.5 + length, "bell": 1.6,
                   "music_box": 1.8, "brass": length * 0.92, "strings": length + 0.2}[lead_name]
        tr.add(lead(hz(midi), sustain), at, pan=-0.1, gain=gain)
        if lead2:
            name2, oct2, g2 = lead2
            fn = INSTRUMENTS[name2]
            s2 = length + 0.3 if name2 == "strings" else 1.4
            tr.add(fn(hz(degree_pitch(tonic, scale, deg) + oct2), s2), at, pan=0.25, gain=gain * g2)

    # фраза из 4 тактов: мотив A (2 такта) + ответ B (2 такта); при повторе фразы
    # гармония другая, поэтому мелодия узнаётся, но не звучит одинаково
    for b in range(intro_bars, final_bar):
        phrase_pos = (b - intro_bars) % 4
        motif = p["motif_a"] if phrase_pos < 2 else p["motif_b"]
        half = phrase_pos % 2  # мотив двухтактный: какую половину играем
        notes, acc = [], 0.0
        for n in motif:  # отбираем ноты нужного такта мотива
            if half * meter <= acc < (half + 1) * meter:
                notes.append((acc - half * meter, n))
            acc += n[0]
        chord = chord_degrees(chords[b], sevenths)
        start = b * bar_s
        for i, (pos, (dur, step, rest)) in enumerate(notes):
            cur = cur + step
            if phrase_pos == 3 and i == len(notes) - 1 and rng.random() < 0.5:
                cur += int(rng.choice([-1, 1]))  # вариация повтора
            if float(pos).is_integer() and (pos == 0 or meter == 4 and pos == 2):
                cur = nearest_chord_tone(cur, chord)  # сильные доли — звуки аккорда
            while cur > 10:
                cur -= 7
            while cur < -2:
                cur += 7
            if not rest:
                play_note(cur, start + pos * beat, min(dur, meter - pos))
    # финал: тоника, долгий звук
    end_at = final_bar * bar_s
    final_deg = 7 if cur > 3 else 0
    play_note(final_deg, end_at, meter * 1.2 if lead_name not in ("brass", "strings") else 2.6 / beat, 0.7)

    # ---------- аккомпанемент ----------
    for b in range(bars):
        deg = chords[b]
        start = b * bar_s
        root = degree_pitch(tonic, scale, deg) % 12 + 36  # бас в районе C2..B2
        notes = chord_midi(deg)
        is_final = b == final_bar
        length = ring if is_final else bar_s

        tr.add(pad([hz(n) for n in notes], length + 0.6), start, gain=0.9 if mood in ("tender", "fairy") else 0.7)
        if is_final:
            tr.add(bass(hz(root), ring * 0.9), start, gain=0.8)
            roll = notes + [notes[0] + 12]
            inst = {"happy": pluck, "festive": brass, "fairy": music_box}.get(mood, piano)
            for k, n in enumerate(roll):
                tr.add(inst(hz(n + (12 if mood == "fairy" else 0)), 2.6), start + k * 0.045,
                       pan=-0.3 + 0.2 * k, gain=0.45)
            if mood in ("happy", "festive"):
                tr.add(kick(), start, gain=0.9)
                tr.add(crash(), start, pan=0.2)
            continue

        drums = b >= intro_bars
        if mood == "happy":
            for pos in (0, 0.75, 1.5, 2, 2.75, 3.5):
                for k, n in enumerate(notes):
                    tr.add(pluck(hz(n), 0.45), start + pos * beat + k * 0.004, pan=-0.25 + 0.25 * k, gain=0.32)
            for i in range(8):
                tr.add(bass(hz(root + (12 if i % 2 else 0)), beat / 2 * 0.9), start + i * beat / 2, gain=0.7)
            if drums:
                for i in range(4):
                    tr.add(kick() if i in (0, 2) else clap(), start + i * beat, gain=0.85)
                for i in range(8):
                    tr.add(hat(level=0.05 if i % 2 else 0.03), start + i * beat / 2, pan=0.35)
        elif mood == "tender":
            tr.add(bass(hz(root), bar_s * 0.95), start, gain=0.6)
            arp = notes + [notes[0] + 12, notes[2], notes[1]]
            for i in range(8):
                tr.add(piano(hz(arp[i % len(arp)]), 1.8), start + i * beat / 2,
                       pan=-0.3 + 0.6 * (i % 2), gain=0.32)
        elif mood == "festive":
            for i in range(4):
                tr.add(bass(hz(root + (7 if i == 2 else 0)), beat * 0.85), start + i * beat, gain=0.7)
                for k, n in enumerate(notes):
                    tr.add(pluck(hz(n + 12), 0.35), start + (i + 0.5) * beat + k * 0.003, pan=0.3, gain=0.22)
            if drums:
                for i in range(4):
                    tr.add(kick(), start + i * beat, gain=0.8)
                    if i in (1, 3):
                        tr.add(clap(), start + i * beat, pan=-0.05, gain=0.8)
                for i in range(16):
                    tr.add(hat(0.06, level=0.04 if i % 4 == 2 else 0.018), start + i * beat / 4, pan=0.4)
            if b % 4 == 3:  # колокольчики в конце фразы
                for i, d in enumerate((7, 9, 11, 14)):
                    tr.add(bell(hz(degree_pitch(tonic, scale, d) + 12), 1.2), start + (2 + i * 0.5) * beat,
                           pan=-0.35, gain=0.35)
        elif mood == "fairy":
            tr.add(bass(hz(root), 2 * beat), start, gain=0.45)
            tr.add(bass(hz(root + 7), 2 * beat), start + 2 * beat, gain=0.35)
            arp = [notes[0], notes[1], notes[2], notes[0] + 12]
            seq = arp + arp[::-1]
            for i in range(8):
                tr.add(bell(hz(seq[i] + 12), 1.0), start + i * beat / 2, pan=0.35 - 0.7 * (i % 2), gain=0.16)
            if drums:
                for i in range(16):
                    tr.add(shaker(0.022 if i % 4 == 0 else 0.012), start + i * beat / 4, pan=0.5)
        elif mood == "romantic":
            tr.add(bass(hz(root), beat * 0.95), start, gain=0.75)
            for i in (1, 2):
                for k, n in enumerate(notes[1:] + [notes[0] + 12]):
                    tr.add(piano(hz(n), 0.9), start + i * beat + k * 0.006, pan=0.2, gain=0.22)
            tr.add(strings(hz(notes[0]), bar_s + 0.3), start, pan=-0.3, gain=0.5)

    rev_s, rev_mix = m["reverb"]
    stereo = synth.reverb(tr.buf, rev_s, rev_mix)
    return finish(stereo, total), total


def finish(stereo, total_seconds):
    """Мастеринг с естественным концом: без длинного затухания, только короткий хвост."""
    stereo = stereo[: int(total_seconds * SR)].copy()
    stereo = np.tanh(stereo * 1.2) / np.tanh(1.2)
    a = int(0.03 * SR)
    stereo[:a] *= np.linspace(0, 1, a)[:, None]
    tail = int(0.6 * SR)
    stereo[-tail:] *= np.linspace(1, 0, tail)[:, None] ** 2
    return stereo / (np.max(np.abs(stereo)) + 1e-9) * 0.89


# ---------- файлы ----------

def melody_bars(mood, seed, name="", fit_seconds=None):
    """Число тактов: под длину голоса (fit_seconds — когда должен закончиться голос) или ~18 с для образца."""
    bpm = plan(mood, seed, name)["bpm"]
    if fit_seconds:
        return bar_count(mood, fit_seconds + 0.6, bpm)
    bar = MOODS[mood]["meter"] * 60 / bpm
    return max(5, round(16 / bar)) + 1


def render_file(out_dir, mood, seed, name="", fit_seconds=None):
    """Сочиняет мелодию и сохраняет mp3 в out_dir (если такой ещё нет). Возвращает путь."""
    bars = melody_bars(mood, seed, name, fit_seconds)
    tag = hashlib.sha1(name.encode()).hexdigest()[:6] if name else "x"
    path = os.path.join(out_dir, f"gen-{mood}-{seed}-{tag}-{bars}.mp3")
    if os.path.exists(path):
        os.utime(path)
        return path
    stereo, _total = compose(mood, seed, bars, name)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2").tobytes()
    tmp = path + ".part.mp3"
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-nostdin", "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", "pipe:0",
         "-codec:a", "libmp3lame", "-b:a", "160k", tmp],
        input=pcm, check=True, capture_output=True,
    )
    os.replace(tmp, path)
    return path


def public_moods():
    return [{"id": k, "title": v["title"], "emoji": v["emoji"], "about": v["about"]} for k, v in MOODS.items()]
