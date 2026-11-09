"""
Открытка из одной фразы: ИИ (GigaChat от Сбера) по короткому описанию
выбирает повод, имена, настроение мелодии, пишет текст поздравления
и может нарисовать картинку для лицевой стороны (GigaChat рисует через Kandinsky).

Ключ: файл ai_key.txt рядом с app.py — одна строка, «Ключ авторизации»
из личного кабинета GigaChat API (developers.sber.ru). Файл не кладём в архивы и git.
Нет ключа, интернета или GigaChat не ответил — текст берётся из встроенных
шаблонов, открытку всё равно можно собрать.

Проверка ключа без сайта:  python ai.py маме на юбилей, любит сад
(на Windows проще запустить check_ai.bat)
"""
import base64
import binascii
import json
import os
import random
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(BASE_DIR, "ai_key.txt")
CERT_DIR = os.path.join(BASE_DIR, "certs")
CERT_FILE = os.path.join(CERT_DIR, "russian_trusted_root_ca.pem")
CERT_URL = "https://gu-st.ru/content/Other/doc/russian_trusted_root_ca.cer"

AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
SCOPE = "GIGACHAT_API_PERS"  # бесплатный доступ для физлиц
# новый адрес API и старый — если первый недоступен, пробуем второй
API_URLS = ["https://api.giga.chat/v1", "https://gigachat.devices.sberbank.ru/api/v1"]
# Pro пишет лучше; если она недоступна или закончились её токены — берём следующую
MODELS = ["GigaChat-2-Pro", "GigaChat-Pro", "GigaChat-2", "GigaChat"]
TIMEOUT = 20      # секунд на один запрос
DEADLINE = 30     # секунд на всё вместе, дальше — шаблон
DRAW_TIMEOUT = 75  # картинка рисуется дольше текста
MAX_PROMPT = 300

OCCASIONS = ("birthday", "newyear", "thanks", "congrats", "love", "custom")
MOODS = ("happy", "tender", "festive", "fairy", "romantic")
OCCASION_MOOD = {"birthday": "happy", "newyear": "fairy", "thanks": "tender",
                 "congrats": "festive", "love": "romantic", "custom": "happy"}

SYSTEM_PROMPT = """Ты помогаешь собрать музыкальную открытку. Человек коротко пишет, кому и по какому поводу открытка.
Ответь ТОЛЬКО одним JSON-объектом, без пояснений и без ```:
{"occasion": "...", "recipient": "...", "sender": "...", "mood": "...", "message": "...", "picture": "..."}

occasion — один из:
  birthday — день рождения, юбилей;
  newyear — Новый год, Рождество, зимние праздники;
  thanks — благодарность;
  congrats — поздравление с событием или достижением: свадьба, выпускной, новая работа, 8 Марта, 23 Февраля, рождение ребёнка;
  love — признание в любви, годовщина отношений, 14 февраля;
  custom — только если не подходит ни один вариант выше: просто так, поддержка, извинение, «выздоравливай», необычный или шуточный праздник.
  Открытка с благодарностью человеку (учителю, ректору, врачу, коллеге) — это thanks.
recipient — кому, в дательном падеже с заглавной буквы: «Маме», «Саше», «Бабушке Вале». Если неясно — "".
sender — от кого, в именительном падеже: «Дима», «Внуки». Если неясно — "".
mood — настроение мелодии, один из: happy (весёлая), tender (нежная, спокойная), festive (праздничная, торжественная), fairy (сказочная, зимняя, волшебная), romantic (романтичная).
message — текст поздравления на русском: 2–4 предложения, от 150 до 260 символов. Тёплый и живой, с деталями из описания, без канцелярита и избитых фраз.
Без обращения в начале и без подписи в конце: «Кому» и «От кого» уже есть на открытке. Без эмодзи и хэштегов.
picture — что нарисовать на лицевой стороне открытки: одно предложение до 200 символов, конкретные предметы, место, время года и цвета по описанию. Без людей крупным планом, без текста и надписей."""

DRAW_PROMPT = """Ты художник поздравительных открыток. Рисуешь тёплые, светлые и красивые иллюстрации
в стиле акварели. На картинке не должно быть текста, букв и надписей."""

_lock = threading.Lock()      # бесплатный GigaChat обрабатывает запросы по одному
_token = {"value": None, "expires": 0.0}
_working = {"api": None, "model": None}
_ssl = {"ctx": None, "verified": True}


class AIError(Exception):
    pass


# ---------- ключ и сертификат ----------

def read_key():
    """Ключ из переменной GIGACHAT_KEY или из ai_key.txt. Пустая строка — ключа нет."""
    key = os.environ.get("GIGACHAT_KEY", "")
    if not key and os.path.exists(KEY_FILE):
        with open(KEY_FILE, encoding="utf-8-sig") as f:
            key = f.read()
    key = key.strip().strip('"').strip("'").strip()
    if key.lower().startswith("basic "):
        key = key[6:].strip()
    return key


def key_looks_valid(key):
    try:
        return ":" in base64.b64decode(key, validate=True).decode("utf-8", "replace")
    except (ValueError, binascii.Error):
        return False


def _load_cert():
    """Корневой сертификат Минцифры: GigaChat подписан им, а в Windows и Python его обычно нет.
    Берём из папки certs/, если его там нет — скачиваем один раз с сайта Госуслуг."""
    if os.path.exists(CERT_FILE):
        return CERT_FILE
    try:
        for name in sorted(os.listdir(CERT_DIR)) if os.path.isdir(CERT_DIR) else []:
            if name.lower().endswith((".cer", ".crt", ".pem")):  # положили файл с Госуслуг вручную
                return _to_pem(os.path.join(CERT_DIR, name))
        with urllib.request.urlopen(CERT_URL, timeout=10) as r:
            data = r.read()
        os.makedirs(CERT_DIR, exist_ok=True)
        tmp = CERT_FILE + ".download"
        with open(tmp, "wb") as f:
            f.write(data)
        return _to_pem(tmp)
    except (OSError, ValueError, ssl.SSLError):
        return None


def _to_pem(path):
    with open(path, "rb") as f:
        data = f.read()
    if b"-----BEGIN CERTIFICATE-----" not in data:
        data = ssl.DER_cert_to_PEM_cert(data).encode("ascii")
    ssl.create_default_context().load_verify_locations(cadata=data.decode("ascii"))  # проверка, что файл годный
    with open(CERT_FILE, "wb") as f:
        f.write(data)
    if path != CERT_FILE and path.endswith(".download"):
        os.remove(path)
    return CERT_FILE


def _context():
    if _ssl["ctx"] is None:
        ctx = ssl.create_default_context()  # системные сертификаты (на Windows — хранилище Windows)
        cert = _load_cert()
        if cert:
            ctx.load_verify_locations(cert)
        _ssl["ctx"] = ctx
    return _ssl["ctx"]


def _open(req, timeout):
    """Запрос с проверкой сертификата. Если сертификат Минцифры так и не нашёлся,
    повторяем без проверки, чтобы демо не сломалось, и предупреждаем в консоли."""
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_context())
    except urllib.error.URLError as err:
        if not isinstance(err.reason, ssl.SSLCertVerificationError):
            raise
        if _ssl["verified"]:
            print("  [ИИ] Не удалось проверить сертификат GigaChat. Работаю без проверки.\n"
                  "       Чтобы исправить: скачайте «Russian Trusted Root CA» с gosuslugi.ru/crt в папку certs.")
            _ssl["verified"] = False
        return urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context())


# ---------- GigaChat ----------

def _get_token(key):
    if _token["value"] and _token["expires"] > time.time() + 60:
        return _token["value"]
    req = urllib.request.Request(AUTH_URL, data=f"scope={SCOPE}".encode(), method="POST", headers={
        "Authorization": f"Basic {key}",
        "RqUID": str(uuid.uuid4()),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    })
    try:
        with _open(req, TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code in (400, 401, 403):
            raise AIError("GigaChat не принял ключ. Проверьте ai_key.txt: нужен «Ключ авторизации».")
        raise AIError(f"GigaChat не выдал доступ (ошибка {err.code}).")
    _token["value"] = data.get("access_token") or data.get("tok")
    expires = data.get("expires_at") or data.get("exp") or 0
    _token["expires"] = expires / 1000 if expires > 1e11 else (expires or time.time() + 1500)
    if not _token["value"]:
        raise AIError("GigaChat прислал пустой ответ на вход.")
    return _token["value"]


def _chat(key, messages, temperature, deadline, timeout=TIMEOUT, extra=None):
    apis = [_working["api"]] if _working["api"] else API_URLS
    models = [_working["model"]] if _working["model"] else MODELS
    last = None
    for api in apis:
        for model in models:
            left = deadline - time.time()
            if left < 3:
                raise AIError("GigaChat отвечает слишком долго.")
            payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": 500}
            payload.update(extra or {})
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(api + "/chat/completions", data=body, method="POST", headers={
                "Authorization": f"Bearer {_get_token(key)}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
            try:
                with _open(req, min(timeout, left)) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                last = err
                if err.code == 401:  # токен истёк раньше срока — в следующий раз возьмём новый
                    _token["value"] = None
                if err.code in (400, 402, 404, 422):
                    continue  # нет такой модели или кончились её токены — пробуем следующую
                raise AIError(f"GigaChat вернул ошибку {err.code}.")
            except urllib.error.URLError as err:
                last = err
                break  # этот адрес недоступен — пробуем следующий
            except (TimeoutError, OSError) as err:
                raise AIError("GigaChat отвечает слишком долго.") from err
            _working["api"], _working["model"] = api, model
            try:
                return data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError):
                raise AIError("GigaChat прислал ответ в непонятном виде.")
    if isinstance(last, urllib.error.HTTPError):
        raise AIError(f"GigaChat вернул ошибку {last.code}.")
    raise AIError("Нет связи с GigaChat. Проверьте интернет.")


def _parse(text, max_message):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AIError("ИИ ответил не по формату.")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        raise AIError("ИИ ответил не по формату.")
    occasion = data.get("occasion") if data.get("occasion") in OCCASIONS else "custom"
    mood = data.get("mood") if data.get("mood") in MOODS else OCCASION_MOOD[occasion]
    message = _clean(data.get("message"))
    if len(message) < 20:
        raise AIError("ИИ не написал текст.")
    return {"occasion": occasion, "mood": mood, "message": _fit(message, max_message),
            "recipient": _clean(data.get("recipient"))[:40], "sender": _clean(data.get("sender"))[:40],
            "picture": _clean(data.get("picture"))[:300]}


def _clean(value):
    value = str(value or "").strip().strip("«»\"' ").strip()
    return re.sub(r"[ \t]+", " ", value)


def _fit(message, limit):
    """Обрезаем длинный текст по концу предложения, чтобы он поместился на открытку."""
    if len(message) <= limit:
        return message
    cut = message[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0] + "…"


# ---------- запасной генератор без интернета ----------

# Слова-триггеры: если они есть во фразе, повод выбирается по ним, что бы ни ответил ИИ.
# Каждое слово ищется с начала слова («лет» не найдётся в «полёт»), \b в конце — только целое слово.
KEYWORDS = [
    ("newyear", ("новым год", "новый год", "новогод", "рождеств", "ёлк", "елк", "зимн")),
    ("birthday", ("день рожд", "днем рожд", "днём рожд", "др\\b", "юбиле", "лет\\b", "годик", "именин")),
    ("love", ("люблю", "любим", "любов", "14 феврал", "валентин", "годовщин", "свидани")),
    ("thanks", ("спасибо", "благодар", "признател", "выручил", "помог")),
    ("congrats", ("поздравл", "свадьб", "выпускн", "диплом", "новой работ", "повышени", "8 март", "23 феврал",
                  "рождением", "новосель", "победой", "сессию")),
]
RELATIONS = [
    ("мам", "Маме"), ("пап", "Папе"), ("бабушк", "Бабушке"), ("бабул", "Бабуле"), ("дедушк", "Дедушке"),
    ("сестр", "Сестре"), ("брат", "Брату"), ("подруг", "Подруге"), ("друг", "Другу"),
    ("коллег", "Коллеге"), ("учител", "Учителю"), ("жен", "Жене"), ("муж", "Мужу"),
    ("сын", "Сыну"), ("доч", "Дочке"), ("начальни", "Начальнику"),
]
TEMPLATES = {
    "birthday": [
        "С днём рождения! Пусть этот год будет щедрым на тёплые встречи, смелые планы и поводы улыбаться. Ты делаешь мир вокруг светлее, и я очень рад(а), что ты есть.",
        "Поздравляю с днём рождения! Желаю здоровья, лёгкости и людей рядом, с которыми хорошо молчать и смеяться. Пусть всё задуманное сбывается вовремя.",
        "С днём рождения! Пусть каждый день нового года жизни приносит маленькую радость, а большие мечты становятся ближе. Обнимаю крепко!",
    ],
    "newyear": [
        "С Новым годом! Пусть он будет тёплым, как плед у окна, и ярким, как гирлянда. Желаю, чтобы всё задуманное сбылось, а рядом были самые близкие.",
        "С наступающим! Пусть Новый год принесёт спокойствие, здоровье и много поводов для радости. Пусть волшебство праздника останется с тобой на весь год.",
        "С Новым годом и Рождеством! Желаю уютных вечеров, смелых начинаний и чудес, которые случаются, когда в них веришь.",
    ],
    "thanks": [
        "Спасибо тебе! Ты оказался(ась) рядом именно тогда, когда это было нужнее всего. Мне очень повезло, что ты есть, и я это ценю.",
        "Хочу просто сказать спасибо. За поддержку, за терпение и за то тепло, которое ты даришь. Это много для меня значит.",
        "Огромное спасибо! Без тебя всё было бы гораздо сложнее. Пусть добро, которое ты делаешь, возвращается к тебе вдвойне.",
    ],
    "congrats": [
        "Поздравляю! Ты это заслужил(а) своим трудом и упорством. Пусть впереди будет ещё больше поводов для гордости и радости.",
        "От всей души поздравляю! Пусть этот день запомнится надолго, а всё новое приносит только хорошее. Я очень рад(а) за тебя!",
        "Поздравляю с этим событием! Желаю вдохновения, сил и людей рядом, которые разделят каждую победу.",
    ],
    "custom": [
        "Просто так, без повода: ты замечательный человек, и мне хочется, чтобы ты знал(а) это. Пусть сегодня будет чуточку теплее, чем вчера.",
        "Эта открытка — маленькое напоминание, что ты важен(на) для меня. Улыбнись, всё обязательно будет хорошо!",
        "Хочу подарить тебе немного радости прямо сейчас. Пусть этот день принесёт приятные сюрпризы и хорошее настроение.",
    ],
    "love": [
        "Просто хочу напомнить, как сильно ты мне дорог(а). С тобой обычные дни становятся особенными, а сложное — простым. Спасибо, что ты рядом.",
        "Каждый день с тобой — маленький праздник. Люблю твой смех, твою заботу и то, как ты умеешь делать мир теплее.",
        "Ты — моё самое тёплое место на свете. Пусть эта открытка напомнит, как много ты для меня значишь.",
    ],
}


def detect_occasion(text):
    """Повод по словам-триггерам или None, если ни одного нет."""
    low = str(text or "").lower().replace("ё", "е")
    for occasion, words in KEYWORDS:
        if any(re.search(r"\b" + w.replace("ё", "е"), low) for w in words):
            return occasion
    return None


def template_card(prompt, previous="", max_message=300):
    low = " " + prompt.lower().replace("ё", "е") + " "
    occasion = detect_occasion(prompt) or "custom"
    recipient = next((dat for stem, dat in RELATIONS if re.search(r"\b" + stem, low)), "")
    options = [t for t in TEMPLATES[occasion] if t != previous] or TEMPLATES[occasion]
    return {"occasion": occasion, "mood": OCCASION_MOOD[occasion], "recipient": recipient, "sender": "",
            "message": _fit(random.choice(options), max_message), "picture": ""}


# ---------- главное ----------

def make_card(prompt, previous="", max_message=300):
    """Собрать открытку по фразе. Возвращает поля открытки + source ("ai" или "template") и note."""
    prompt = re.sub(r"\s+", " ", str(prompt or "")).strip()[:MAX_PROMPT]
    previous = _clean(previous)[:600]
    if len(prompt) < 3:
        raise AIError("Напишите пару слов: кому открытка и по какому поводу.")
    key = read_key()
    if not key:
        note = "ИИ не подключён (нет файла ai_key.txt), текст взят из шаблонов."
    elif not key_looks_valid(key):
        note = "Ключ в ai_key.txt не похож на «Ключ авторизации» GigaChat, текст взят из шаблонов."
    else:
        user = f"Описание: {prompt}"
        if previous:
            user += f"\nПредыдущий вариант текста был таким, напиши совсем другой: {previous}"
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
        started = time.time()
        try:
            with _lock:
                answer = _chat(key, messages, 0.95 if previous else 0.7, started + DEADLINE)
            card = _parse(answer, max_message)
            # слово-триггер во фразе важнее выбора ИИ; если ИИ выбрал «свой повод»,
            # а по тексту видно обычный (например, «спасибо»), берём обычный
            card["occasion"] = (detect_occasion(prompt) or
                                (detect_occasion(card["message"]) if card["occasion"] == "custom" else None) or
                                card["occasion"])
            card.update(source="ai", note="", seconds=round(time.time() - started, 1))
            return card
        except (AIError, OSError, ValueError) as err:
            reason = str(err) if isinstance(err, AIError) else "Нет связи с GigaChat. Проверьте интернет."
            note = f"{reason} Текст взят из шаблонов."
            print(f"  [ИИ] {reason}" + ("" if isinstance(err, AIError) else f" ({err})"))
    card = template_card(prompt, previous, max_message)
    card.update(source="template", note=note)
    return card


def _download(file_id, deadline):
    req = urllib.request.Request(f"{_working['api']}/files/{file_id}/content", headers={
        "Authorization": f"Bearer {_get_token(read_key())}",
        "Accept": "application/jpg",
    })
    try:
        with _open(req, max(5, min(TIMEOUT, deadline - time.time()))) as r:
            return r.read(15 * 1024 * 1024)
    except urllib.error.HTTPError as err:
        raise AIError(f"Не удалось скачать картинку (ошибка {err.code}).")


def draw_picture(description):
    """Нарисовать картинку для лицевой стороны. Возвращает байты JPEG или бросает AIError.
    Запасного варианта нет: без картинки открытка просто остаётся как была."""
    description = re.sub(r"\s+", " ", str(description or "")).strip()[:MAX_PROMPT]
    if len(description) < 3:
        raise AIError("Нечего рисовать: сначала создайте открытку с ИИ.")
    key = read_key()
    if not key or not key_looks_valid(key):
        raise AIError("Картинку рисует только ИИ, а ключа GigaChat нет (ai_key.txt).")
    messages = [{"role": "system", "content": DRAW_PROMPT},
                {"role": "user", "content": f"Нарисуй картинку для открытки: {description}"}]
    deadline = time.time() + DRAW_TIMEOUT + 15
    try:
        with _lock:
            answer = _chat(key, messages, 0.8, deadline, DRAW_TIMEOUT, {"function_call": "auto"})
            found = re.search(r'<img[^>]*src="([^"]+)"', answer)
            if not found:
                raise AIError("ИИ не стал рисовать эту картинку. Попробуйте другую фразу.")
            data = _download(found.group(1), deadline)
    except (OSError, ValueError) as err:
        print(f"  [ИИ] картинка: {err}")
        raise AIError("Нет связи с GigaChat, картинку нарисовать не удалось.")
    if not data.startswith(b"\xff\xd8\xff") and not data.startswith(b"\x89PNG"):
        raise AIError("GigaChat прислал не картинку.")
    return data


def status_line():
    key = read_key()
    if not key:
        return "ИИ (GigaChat): ключа нет, работают шаблоны. Положите ключ в ai_key.txt"
    if not key_looks_valid(key):
        return "ИИ (GigaChat): ключ в ai_key.txt выглядит неправильно, нужен «Ключ авторизации»"
    return "ИИ (GigaChat): ключ найден"


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    text = " ".join(sys.argv[1:]) or "маме на юбилей, она любит сад и тёплые летние вечера"
    print(status_line())
    print("Фраза:", text)
    t = time.time()
    result = make_card(text)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Время: {time.time() - t:.1f} с, источник: {'GigaChat' if result['source'] == 'ai' else 'шаблоны'}")
    if result["source"] == "ai" and result.get("picture"):
        print("Рисую картинку…")
        t = time.time()
        try:
            data = draw_picture(result["picture"])
            path = os.path.join(BASE_DIR, "ai_test_picture.jpg")
            with open(path, "wb") as f:
                f.write(data)
            print(f"Картинка готова за {time.time() - t:.1f} с: {path}")
        except AIError as err:
            print("Картинка:", err)
