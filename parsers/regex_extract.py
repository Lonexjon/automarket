"""
Бесплатный разбор объявлений из telegram_raw через regex -- без обращения
к LLM вообще. Покрывает посты с понятным эмодзи-шаблоном полей, который
используют почти все каналы в выборке:

    #Nexia 3
    📅 Йили: 2020   (или "Yili:", "Yil:")
    👣 Пробег: 97000 km   (или "Probeg:")
    💰 Narxi: 8700$   (или просто "8,700$" / "195000000 сум")
    ☎️ Тел: +998...   (или "Tel:")

Посты, которые НЕ разбираются этим парсером (нет цены/года/пробега в
понятном формате), остаются необработанными -- их можно позже прогнать
через llm_extract.py как fallback, когда будет бюджет, или просто
пропустить, если regex уже покрывает достаточно.

Использование:
  python parsers/regex_extract.py            # разобрать всё новое
  python parsers/regex_extract.py 50         # ограничить (тест)
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import money  # noqa: E402 -- извлечение/классификация денежных значений, см. money.py

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")
SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")

# Год: раньше было ограничено 2000-2029 -- реальный рынок включает и более
# старые машины (GAZ 53 1990 года, Volga 1962 года -- см. health_check.py,
# он уже принимает 1970-2029, а regex молча отбрасывал такие посты).
# Раздельно "с явным маркером года" (Йили:/Yili:/год) и запасной вариант
# "любое 4-значное число похожее на год" -- запасной вариант специально Уже
# (1990-2029), чтобы не путать год с чем угодно другим 4-значным.
YEAR_RE = re.compile(r"(?:йили|yili|yil|год|г\.в\.?)\s*[:\-]?\s*(19[7-9]\d|20[0-2]\d)", re.I)
YEAR_FALLBACK_RE = re.compile(r"\b(19[9]\d|20[0-2]\d)\b")

# \s внутри классов ниже, а не литеральный пробел -- в тексте, скопированном
# из Telegram/таблиц, разделителем тысяч часто оказывается неразрывный
# пробел (\xa0), который literal ' ' не матчит. С literal ' ' "20\xa0000 $"
# терял ведущую "20" и давал price_usd=0.0 -- тихий баг, без исключения
# (0.0 потом схлопывался в NULL через "or None", объявление тихо теряло цену).

# пробег: "97000 km" / "97,000 км" / "Probeg: 97.000 km"
MILEAGE_RE = re.compile(
    r"(?:probeg|пробег)\s*[:\-]?\s*([\d][\d,.\s]{2,10})\s*(?:km|км)", re.I
)

# Извлечение и классификация цены -- см. money.py. Старые PRICE_USD_RE/
# PRICE_UZS_RE (искали только ПЕРВОЕ число перед $/so'm) заменены на
# money.resolve_price(), которая находит ВСЕ денежные упоминания и решает,
# какое из них (если есть) действительно полная цена -- см. docstring
# money.py про "Boshiga 2,000$ — 13 oyga 500$" и другие реальные утечки.

# телефон: +998 followed by 9 digits, с разделителями или без
PHONE_RE = re.compile(r"(\+?998[\s\-]?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})")

# марка/модель из первого хэштега: "#Nexia 3" / "#Chevrolet_Cobalt"
HASHTAG_RE = re.compile(r"#([A-Za-zА-Яа-яЎўҚқҒғҲҳ]+)")

TRANSMISSION_AUTO_RE = re.compile(r"\bavtomat\b|\bавтомат\b", re.I)
TRANSMISSION_MANUAL_RE = re.compile(r"\bmexanika\b|\bмеханика\b|\bмех\.?\b", re.I)

# Флаги повреждений/аварий -- только по явным упоминаниям в тексте
# (source="text" в терминах openapi.yaml). Отсутствие совпадения НЕ значит
# "не битая" -- значит просто "не упомянуто", это не подтверждение чистоты.
FLAG_PATTERNS = [
    ("accident_mentioned", "Упоминается авария/ДТП", "warning", re.compile(
        r"avariya|авари|после\s*дтп|\bдтп\b", re.I)),
    ("painted_mentioned", "Упоминается покраска/крашеные элементы", "warning", re.compile(
        r"boyalgan|бўялган|крашен|перекраш", re.I)),
    ("hit_mentioned", "Упоминается удар/повреждение кузова", "warning", re.compile(
        r"urilgan|урилган|\bбит[аоы]\b|битый", re.I)),
    ("needs_repair_mentioned", "Упоминается требуемый ремонт", "warning", re.compile(
        r"ta'mirtalab|ремонт\s*треб|требует\s*ремонта", re.I)),
    ("taxi_mentioned", "Упоминается такси/корпоративное использование", "warning", re.compile(
        r"\bтакси\b|\btaksi\b|korporativ|корпоратив", re.I)),
    ("documents_issue_mentioned", "Упоминаются проблемы с документами/растаможкой", "warning", re.compile(
        r"hujjatlar\s*muammo|документ[аоы]?\s*проблем|не\s*растаможен|rastamojka\s*yo'q", re.I)),
    ("pledge_mentioned", "Упоминается залог/непогашенный кредит", "warning", re.compile(
        r"garov|залог|kredit(?:da|га)?\s*(?:tolan|туриб)|кредит\s*не\s*выплач", re.I)),
    # Если в тексте есть "рассрочка" -- извлечённая цена почти наверняка
    # первый взнос, а не полная стоимость машины. deal_score.py отдельно
    # исключает объявления с этим флагом из медианы/собственного deal_score
    # (см. INSTALLMENT_FLAG_CODE там) -- иначе они выглядят как "супер-цена",
    # хотя на деле это просто часть суммы.
    #
    # На реальных данных слово "рассрочка"/"rassrochka" почти НЕ встречается
    # (проверено: 0 сработавших на всей базе) -- продавцы пишут по-другому,
    # без единого "стоп-слова". Настоящий паттерн-утечка, который реально
    # пропускался: "Boshiga 2,000$ — 13 oyga 500$" -- пост вообще без строки
    # "Narxi:", PRICE_USD_RE хватает первую сумму по тексту, а это и есть
    # первый взнос (boshiga), а не цена. bo'lib+oy тут первый явный сигнал.
    # nasiya/насия ("рассрочка/кредит от продавца") -- тоже сигнал, но часто
    # встречается в ОТРИЦАНИИ ("nasiya yo'q" = рассрочки нет, цена настоящая,
    # полная) -- на это и рассчитан NEGATION_RE ниже, не убирать из-под него.
    ("installment_price_mentioned", "Цена может быть первым взносом по рассрочке, не полной стоимостью", "warning", re.compile(
        r"rassrochka|рассрочк|bo'lib[- ]bo'lib|oyiga\s*to'lov|oyma-oy"
        r"|boshiga\s*[\d][\d,.\s]{0,10}\s*\$?.{0,40}\boyga\b"
        r"|nasiya|насия", re.I)),
    # Каналы массово репостят "объявление УЖЕ ПРОДАНО" как отдельный жанр
    # постов (соцдоказательство: "2012 yil Nexia2 40 mlnga baraka bo'pti
    # tabriklaymiz" -- "продано, поздравляем"). Часть из них -- чистые
    # поздравления без реальных данных о машине (их try_extract просто не
    # вставляет, см. is_sold_confirmation ниже). Но другая часть -- РЕАЛЬНЫЕ
    # структурированные объявления (с Narxi:/Йили:), к которым администратор
    # ПОЗЖЕ дописал "Tel: #Sotildi 15300$" -- такие уже попадают в listings
    # как будто машина всё ещё продаётся. Этот флаг ловит именно их.
    ("sold_mentioned", "Объявление помечено как уже проданное", "critical", re.compile(
        r"\bsotildi\b|сотилди|СОТИЛДИ|baraka\s*bo'?pti|барака\s*б[уў]?пти"
        r"|#\s*[Ss]otildi", re.I)),
]

# Отдельные "постов-поздравлений" НЕ являются объявлениями вообще -- канал
# просто сообщает, что чья-то машина продалась ("Aka mashina sotildi
# rahmat... baraka bo'pti tabriklaymiz"). У них нет структурированных полей
# (Narxi:/Йили:/Probeg:) -- если такой маркер в тексте ЕСТЬ, это настоящее
# объявление (просто с поздним "продано"-примечанием), и его штамповать в
# listings можно (просто с флагом sold_mentioned выше). Если структурных
# маркеров НЕТ вообще -- это чистое поздравление, try_extract() отбрасывает
# его целиком, как и раньше (до сих пор это происходило случайно, из-за
# отсутствия цены/бренда -- теперь после того как гейт "нужна цена" снят,
# без этой явной проверки такие посты стали бы вставляться как объявления
# с NULL-ценой, что хуже, чем не вставлять вовсе).
STRUCTURED_MARKER_RE = re.compile(
    r"narxi\s*[:\-]|нарх[иа]\s*[:\-]|yili\s*[:\-]|йили\s*[:\-]"
    r"|probeg\s*[:\-]|пробег\s*[:\-]|yurgani\s*[:\-]|юрган[аи]?\s*[:\-]",
    re.I,
)
SOLD_ONLY_RE = re.compile(
    r"\bsotildi\b|сотилди|СОТИЛДИ|baraka\s*bo'?pti|барака\s*б[уў]?пти",
    re.I,
)


def is_pure_sold_confirmation(text: str) -> bool:
    """True для постов-поздравлений о продаже без единого структурного поля
    -- это не объявление, а социальное подтверждение, вставлять не нужно."""
    return bool(SOLD_ONLY_RE.search(text)) and not STRUCTURED_MARKER_RE.search(text)


# Реальная утечка на проде, найдена владельцем в браузере -- четыре разных
# объявления ("Матиз Арендага берилади", "Дамас Арендага берилади", "Нексия
# 3 Арендага берилади" и т.д.), все с одной и той же фразой "Арендага
# берилади" ("сдаётся в аренду"). Это посты об АРЕНДЕ, не о продаже -- их
# "Нархи: 300$ залог" это депозит + посуточная ставка, а не цена покупки
# машины. Раньше они разбирались тем же путём, что и объявления о продаже,
# и депозит становился price_usd, будто это честная цена продажи. Сайт --
# маркетплейс продажи, а не аренды; для аренды нет самого понятия "цена
# покупки", так что пытаться её извлечь всегда даёт мусор, а не просто
# неуверенный результат. Требуем ИМЕННО пару "аренда"+"берилади"
# ("сдаётся"), а не голое слово "аренда" -- иначе рискуем ложно исключить
# объявление о продаже, которое просто упоминает, что машина НЕ была в
# аренде/такси.
RENTAL_RE = re.compile(
    r"аренда(?:га)?\s*берилади|arenda(?:ga)?\s*beriladi"
    r"|ижарага\s*берилади|ijaraga\s*beriladi"
    # "#Arendaga Нексия 3 ... оламан" -- канал сам хэштегом маркирует пост
    # как аренду; здесь это не "сдаётся", а "ищу взять в аренду" (другой
    # смысл фразы "берилади" выше), но категория та же -- не объявление о
    # продаже, цены покупки у него нет по определению. Хэштег в начале
    # поста -- надёжный сигнал сам по себе, отдельный от текстовой фразы.
    r"|#\s*арендага\b|#\s*arendaga\b|#\s*ижарага\b|#\s*ijaraga\b",
    re.I,
)


def is_rental_listing(text: str) -> bool:
    """True для постов об аренде (не продаже) машины -- см. RENTAL_RE."""
    return bool(RENTAL_RE.search(text))

# Отрицание рядом со словом переворачивает смысл ("avariyaga uchramagan" =
# НЕ была в аварии, "не крашена" = НЕ крашена) -- если рядом с совпадением
# есть один из этих маркеров, флаг не ставим вообще (не знаем точно, что
# там было, но точно не positive-утверждение о повреждении).
#
# "йўқ"/"йук" -- кириллический узбекский вариант "yo'q" ("нет"), встречается
# в текстах ничуть не реже латиницы (было пропущено -- "банк йўқ, насия йўқ"
# не распознавалось как отрицание вообще, флаг ставился на чистую цену).
NEGATION_RE = re.compile(
    r"\bне\s|\bбез\s|uchramagan|bo'?lmagan|bulmagan|emas\b|yo'?q\b|siz\b"
    r"|йўқ|йук", re.I
)
NEGATION_WINDOW = 20  # символов до/после совпадения, где ищем отрицание


def detect_flags(text: str) -> list[dict]:
    flags = []
    for code, label, severity, pattern in FLAG_PATTERNS:
        for m in pattern.finditer(text):
            window = text[max(0, m.start() - NEGATION_WINDOW): m.end() + NEGATION_WINDOW]
            if NEGATION_RE.search(window):
                continue  # отрицание рядом -- пропускаем это совпадение
            flags.append({"code": code, "label": label, "severity": severity, "source": "text"})
            break  # одного совпадения достаточно, дальше не ищем по этому паттерну
    return flags

# tag -> (brand, model). Большинство тегов в каналах -- это на самом деле
# название МОДЕЛИ (Cobalt, Nexia, Malibu -- всё это модели Chevrolet в
# узбекской линейке), а не марки. Раньше это писалось прямо в поле "brand",
# что путало сегментацию (объявления с #Cobalt не находились по фильтру
# "Chevrolet"). Теперь разносим по обоим полям.
BRAND_MODEL_MAP: dict[str, tuple[str, str | None]] = {
    "chevrolet": ("chevrolet", None),
    "kia": ("kia", None),
    "hyundai": ("hyundai", None),
    "daewoo": ("daewoo", None),
    "nexia": ("chevrolet", "nexia"),
    "cobalt": ("chevrolet", "cobalt"),
    "malibu": ("chevrolet", "malibu"),
    "gentra": ("chevrolet", "gentra"),
    "spark": ("chevrolet", "spark"),
    "damas": ("chevrolet", "damas"),
    "tracker": ("chevrolet", "tracker"),
    "onix": ("chevrolet", "onix"),
    "captiva": ("chevrolet", "captiva"),
    "equinox": ("chevrolet", "equinox"),
    "lacetti": ("chevrolet", "lacetti"),
    "matiz": ("chevrolet", "matiz"),
    "orlando": ("chevrolet", "orlando"),
    "trailblazer": ("chevrolet", "trailblazer"),
    "tahoe": ("chevrolet", "tahoe"),
    "optra": ("chevrolet", "optra"),
    "monza": ("chevrolet", "monza"),
    "trax": ("chevrolet", "trax"),
    "blazer": ("chevrolet", "blazer"),
    "aveo": ("chevrolet", "aveo"),
    "epica": ("chevrolet", "epica"),
    # Kia
    "k5": ("kia", "k5"), "sportage": ("kia", "sportage"), "seltos": ("kia", "seltos"),
    "sorento": ("kia", "sorento"), "cerato": ("kia", "cerato"), "rio": ("kia", "rio"),
    "soluto": ("kia", "soluto"), "picanto": ("kia", "picanto"), "carnival": ("kia", "carnival"),
    # Hyundai
    "elantra": ("hyundai", "elantra"), "sonata": ("hyundai", "sonata"),
    "tucson": ("hyundai", "tucson"), "santafe": ("hyundai", "santa fe"),
    "accent": ("hyundai", "accent"), "creta": ("hyundai", "creta"),
    "solaris": ("hyundai", "solaris"),
}

# Города Узбекистана -- по хэштегу/тексту, для фильтра на сайте. Список не
# исчерпывающий, покрывает области, где сидят каналы из telegram_channels.md.
CITY_MAP: dict[str, str] = {
    "toshkent": "Ташкент", "ташкент": "Ташкент",
    "samarqand": "Самарканд", "самарканд": "Самарканд",
    "andijon": "Андижан", "андижан": "Андижан",
    "fargona": "Фергана", "фаргона": "Фергана", "фергана": "Фергана",
    "namangan": "Наманган", "наманган": "Наманган",
    "buxoro": "Бухара", "бухара": "Бухара",
    "xorazm": "Хорезм", "хоразм": "Хорезм", "urganch": "Ургенч",
    "qarshi": "Карши", "карши": "Карши",
    "termiz": "Термез", "термез": "Термез",
    "navoiy": "Навои", "навои": "Навои",
    "jizzax": "Джизак", "джизак": "Джизак",
    "guliston": "Гулистан", "гулистан": "Гулистан",
    "nukus": "Нукус", "нукус": "Нукус",
    "qoqon": "Коканд", "коканд": "Коканд",
    "chirchiq": "Чирчик", "чирчик": "Чирчик",
    "angren": "Ангрен", "ангрен": "Ангрен",
    "bekobod": "Бекабад", "бекабад": "Бекабад",
    "margilon": "Маргилан", "маргилан": "Маргилан",
    "denov": "Денау", "денау": "Денау",
    "shahrisabz": "Шахрисабз", "шахрисабз": "Шахрисабз",
    "kattaqorgon": "Каттакурган", "каттакурган": "Каттакурган",
    "olmaliq": "Алмалык", "алмалык": "Алмалык",
}


def normalize_number(raw: str) -> float:
    digits = re.sub(r"[^\d]", "", raw)
    return float(digits) if digits else 0.0


def guess_brand_model(text: str) -> tuple[str | None, str | None]:
    for tag in HASHTAG_RE.findall(text):
        low = tag.lower()
        if low in BRAND_MODEL_MAP:
            return BRAND_MODEL_MAP[low]
    low_text = text.lower()
    for tag, (brand, model) in BRAND_MODEL_MAP.items():
        if tag in low_text:
            return brand, model
    return None, None


def guess_city(text: str) -> str | None:
    for tag in HASHTAG_RE.findall(text):
        low = tag.lower()
        if low in CITY_MAP:
            return CITY_MAP[low]
    low_text = text.lower()
    for tag, city in CITY_MAP.items():
        if tag in low_text:
            return city
    return None


def phone_hash(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"[^\d]", "", phone)
    if len(digits) < 7:
        return None
    return hashlib.sha256(digits.encode()).hexdigest()


def try_extract(text: str) -> dict | None:
    """Возвращает словарь полей, если удалось разобрать, иначе None.

    ВАЖНО: раньше здесь был жёсткий гейт "нет цены -- не пытаемся вообще".
    Теперь цена не обязательна -- если у поста есть бренд/год, но цену
    уверенно определить нельзя, объявление всё равно сохраняется с
    price_usd=NULL, price_type/needs_review объясняют почему (см. money.py).
    Жёсткие отказы: чистые посты-поздравления о продаже без единого
    структурного поля (is_pure_sold_confirmation), и посты об аренде, а не
    продаже (is_rental_listing) -- у аренды нет цены покупки как понятия,
    депозит/посуточная ставка не заменяют её ни при каком needs_review."""
    if is_pure_sold_confirmation(text):
        return None
    if is_rental_listing(text):
        return None

    year_m = YEAR_RE.search(text) or YEAR_FALLBACK_RE.search(text)
    mileage_m = MILEAGE_RE.search(text)
    phone_m = PHONE_RE.search(text)
    brand, model = guess_brand_model(text)
    city = guess_city(text)
    price = money.resolve_price(text)

    transmission = None
    if TRANSMISSION_AUTO_RE.search(text):
        transmission = "automatic"
    elif TRANSMISSION_MANUAL_RE.search(text):
        transmission = "manual"

    # без бренда, без года и без ЛЮБОЙ денежной зацепки -- слишком
    # неуверенно, точно нечего сохранять.
    if not brand and not year_m and price.price_reason == "no_money_found":
        return None

    return {
        "brand": brand,
        "model": model,
        "city": city,
        "year": int(year_m.group(1)) if year_m else None,
        "mileage_km": int(normalize_number(mileage_m.group(1))) if mileage_m else None,
        "price_usd": price.price_usd,
        "price_uzs": price.price_uzs,
        "price_type": price.price_type,
        "price_confidence": price.price_confidence,
        "needs_review": price.needs_review,
        "price_reason": price.price_reason,
        "transmission": transmission,
        "phone": phone_m.group(1) if phone_m else None,
    }


def ensure_schema(con: sqlite3.Connection) -> None:
    # Идемпотентная миграция: ALTER TABLE ... ADD COLUMN на существующей
    # базе кидает OperationalError, если колонка уже есть -- ловим и
    # продолжаем. Ничего не удаляем и не переименовываем, старые данные не
    # трогаются, значения новых колонок NULL для уже вставленных строк
    # (backfill делает отдельный tools/reprocess_prices.py, не эта функция).
    for col, decl in [
        ("flags", "TEXT"),
        ("price_type", "TEXT"),        # full_price|down_payment|monthly_payment|exchange_addition|installment|negotiable|unknown
        ("price_confidence", "TEXT"),  # high|medium|low
        ("needs_review", "INTEGER"),   # 0/1
        ("price_reason", "TEXT"),      # причина/метод решения (money.py PriceResult.price_reason)
    ]:
        try:
            con.execute(f"ALTER TABLE listings ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass  # уже есть, или таблицы ещё нет (создастся ниже)
    con.commit()

    with open(SCHEMA_PATH) as f:
        con.executescript(f.read())
    con.commit()


def fetch_unprocessed(con: sqlite3.Connection, limit: int | None):
    query = """
        SELECT r.channel, r.message_id, r.posted_at, r.text
        FROM telegram_raw r
        LEFT JOIN listings l ON l.source = 'telegram' AND l.source_id = (r.channel || ':' || r.message_id)
        WHERE l.id IS NULL AND r.text IS NOT NULL AND r.text != ''
        ORDER BY r.posted_at DESC
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    return con.execute(query).fetchall()


def main(limit: int | None):
    con = sqlite3.connect(DB_PATH)
    ensure_schema(con)
    rows = fetch_unprocessed(con, limit)
    print(f"К разбору: {len(rows)} постов (бесплатно, без LLM)\n")

    saved, unmatched = 0, 0
    now = datetime.now(timezone.utc).isoformat()

    for channel, message_id, posted_at, text in rows:
        data = try_extract(text)
        if not data:
            unmatched += 1
            continue

        listing_id = f"tg_{uuid.uuid4().hex[:10]}"
        source_id = f"{channel}:{message_id}"
        source_url = f"https://t.me/{channel}/{message_id}"

        flags = detect_flags(text)
        # Объявление со структурными полями, к которому позже дописали
        # "продано" (sold_mentioned) -- мы всё ещё сохраняем его (цена и
        # данные полезны для медианы сегмента и истории), но по решению
        # владельца больше не показываем в живой ленте: покупателю
        # неактуальное "продано" объявление не нужно. Ставим removed_at
        # сразу при вставке, а не только флаг -- чтобы новые такие
        # объявления не нужно было чистить отдельным прогоном.
        removed_at = now if any(f["code"] == "sold_mentioned" for f in flags) else None

        con.execute(
            """INSERT INTO listings (
                id, source, source_id, source_url, category, title,
                price_usd, price_uzs, price_type, price_confidence, needs_review, price_reason,
                city, brand, model, year, mileage_km,
                transmission, description_raw, flags, phone_hash,
                posted_at, first_seen_at, last_seen_at, removed_at
            ) VALUES (?, 'telegram', ?, ?, 'cars', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO NOTHING""",
            (
                listing_id, source_id, source_url,
                f"{data.get('brand') or ''} {data.get('model') or ''} {data.get('year') or ''}".strip() or "Без названия",
                data["price_usd"], data["price_uzs"], data["price_type"],
                data["price_confidence"], int(data["needs_review"]), data["price_reason"],
                data["city"], data["brand"], data["model"], data["year"], data["mileage_km"],
                data["transmission"], text, json.dumps(flags, ensure_ascii=False) if flags else None,
                phone_hash(data["phone"]), posted_at, now, now, removed_at,
            ),
        )
        con.commit()
        saved += 1

    con.close()
    print(f"Разобрано regex'ом: {saved}")
    print(f"Не подошли под шаблон (кандидаты для LLM позже): {unmatched}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    main(limit)
