"""
Извлечение и классификация денежных значений из текста объявления.

Используется regex_extract.py -- это не отдельный параллельный парсер, а
вынесенный (для тестируемости) шаг существующего пайплайна: "найти все
денежные упоминания в тексте -> классифицировать контекст каждого ->
выбрать, какое из них (если есть) действительно полная цена машины".

Почему это отдельный модуль, а не просто более сложный regex в
regex_extract.py: старая логика (`PRICE_USD_RE.search()`) брала ПЕРВОЕ
попавшееся число перед $/so'm и считала его ценой. На реальных данных это
систематически ловит не то число:

  "Boshiga 2,000$ — 13 oyga 500$"       -- первое число это ПЕРВЫЙ ВЗНОС
  "Narxi: 15,600$ | Tel: #Sotildi 15300$" -- второе число это цена ПРОДАЖИ
  "12,000$ ками бор ... 16,500$ га кридитини ечиб берамиз" -- два разных
                                             числа, оба не факт что "цена"

Правило, которое нельзя нарушать (явное требование владельца проекта):
первоначальный взнос и ежемесячный платёж НИКОГДА не должны стать
`price_usd`/`price_uzs`. Если уверенной полной цены нет -- цена остаётся
NULL, а не "лучшее из того, что нашли".
"""
import re
from dataclasses import dataclass, field

# --- нормализация текста -----------------------------------------------

# Апострофы/кавычки в узбекской латинице пишут кто во что горазд:
# o'g'lik можно увидеть с ' (U+0027), ' (U+2018), ' (U+2019), ` (U+0060),
# ʻ (U+02BB), ʼ (U+02BC). Нормализуем все варианты к обычному ' перед любым
# regex-матчингом -- иначе "so'm" / "so'm" / "so`m" -- три разных паттерна.
_APOSTROPHE_VARIANTS = "‘’ʻʼ`´"
_APOSTROPHE_RE = re.compile("[" + _APOSTROPHE_VARIANTS + "]")

# Неразрывный пробел и похожие пробельные символы -- частый гость в тексте,
# скопированном из Telegram/таблиц.
_SPACE_VARIANTS_RE = re.compile("[    ]")


def normalize_text(text: str) -> str:
    """Приводит апострофы и нестандартные пробелы к обычным ASCII-вариантам.
    Не трогает регистр и не транслитерирует -- только нормализация символов,
    которые визуально одинаковы, но байтово разные."""
    text = _APOSTROPHE_RE.sub("'", text)
    text = _SPACE_VARIANTS_RE.sub(" ", text)
    return text


# --- поиск денежных значений --------------------------------------------

# Число с разделителями тысяч (запятая, точка, пробел, апостроф) -- "8,700"
# / "8.700" / "8 700" / "22.700" / "8'700" (апостроф как разделитель тоже
# встречается; normalize_text() уже свёл все варианты апострофа к "'").
# ОБЯЗАТЕЛЬНО заканчивается на цифру -- иначе "$15000, первоначальный..."
# жадно проглатывал ", " в само число (разделители разрешены только МЕЖДУ
# цифрами), сдвигая границу контекстного окна вглубь следующего слова и
# обрезая маркер down_payment/monthly на середине.
_NUM = r"\d(?:[\d,.\s']{0,9}\d)?"

# Множители словом: "315 mln so'm" = 315 000 000 сум, "950ming" = 950 000,
# "100 million" = 100 000 000. Раньше это не парсилось вообще -- PRICE_UZS_RE
# требовал 5-15 ЦИФР перед сум/so'm, а тут только 3 цифры + слово-множитель.
_MAGNITUDE = {
    "mln": 1_000_000, "млн": 1_000_000, "million": 1_000_000,
    "миллион": 1_000_000, "миллиона": 1_000_000, "миллионов": 1_000_000,
    "ming": 1_000, "минг": 1_000, "тыс": 1_000, "тысяч": 1_000,
    "тысячи": 1_000,
}
_MAGNITUDE_ALT = "|".join(sorted(_MAGNITUDE, key=len, reverse=True))
_CURRENCY_UZS = r"(?:сум|со[мm]|so'?m)"

# $-сумма: символ до ИЛИ после числа -- "16.500$" (было) и "$22.700" (не
# было, реальная утечка: xolis_mashina_bozor писал именно так).
MONEY_USD_RE = re.compile(
    rf"(?:\$\s*({_NUM}))|(?:({_NUM})\s*(?:\$|у\.?\s?е\.?|y\.?\s?e\.?))",
    re.I,
)

# Сумовая сумма. Валюта-суффикс ("сум"/"so'm") обязателен, ЕСЛИ нет
# слова-множителя -- но если множитель есть ("315 mln", "950ming"), суффикс
# необязателен: реальные посты часто пишут "Narxi: 950ming oxiri" или
# "100 million kami bor" вообще без слова "сум" -- контекст (Narxi:, размер
# числа) однозначно говорит, что это сумы, не доллары никто в этом
# сегменте не считает миллионами.
MONEY_UZS_RE = re.compile(
    rf"({_NUM})\s*(?:(?:({_MAGNITUDE_ALT}))\s*{_CURRENCY_UZS}?|{_CURRENCY_UZS})",
    re.I,
)

# Контекст-маркеры вокруг найденного числа -- решают, что это за деньги.
DOWN_PAYMENT_RE = re.compile(
    r"boshiga|бошига|birinchi\s*(?:vznos|to'?lov)|перв(?:ый|оначальный)\s*(?:взнос|платеж|платёж)"
    r"|boshlang'?ich|задаток|zadatok|\bavans\b|аванс", re.I,
)
MONTHLY_RE = re.compile(
    r"oy(?:i|)ga\b|ойига|ойга\b|\bв\s*месяц\b|/\s*мес\b|oyma-oy"
    r"|ежемесячн", re.I,
)
# "13 oy 300$" -- реальный вариант без суффикса "-ga" (см. tg_4df678c78d на
# проде: "Boshiga 2,000$ 13 oy 350$", 350$ -- ежемесячный платёж). Бере тся
# ОТДЕЛЬНО от MONTHLY_RE и с намного более узким радиусом действия
# (MONTHLY_BARE_MAX_DISTANCE, не общий MAX_MARKER_DISTANCE): голое "N oy"
# само по себе означает просто "N месяцев" (срок кредита), а не "это число
# -- платёж за месяц" -- в "12,000$ ... 10 ой кридити бор ... 16,500$" "10
# ой" описывает СРОК кредита, а не то, что 12,000$ -- ежемесячный платёж.
# Только когда "N oy" стоит вплотную перед суммой, это осмысленно читается
# как "N oy <сумма>" = помесячный платёж.
MONTHLY_BARE_RE = re.compile(r"\d\s*oy\b|\d\s*ой\b", re.I)
MONTHLY_BARE_MAX_DISTANCE = 6
EXCHANGE_RE = re.compile(
    r"doplata|доплата|almash\w*.{0,15}doplata|обмен.{0,15}доплат"
    r"|бартер.{0,15}доплат|almashinuv", re.I,
)
NEGOTIABLE_RE = re.compile(
    r"kelishiladi|kelishamiz|келишамиз|келишилади|договорн|\btorg\b|торг", re.I,
)
FULL_PRICE_LABEL_RE = re.compile(
    r"нарх[иа]?|narxi|narx\b|цена\b|стоимост", re.I,
)

# Реальная утечка на проде (tg_afdf96e1d5): "Нархи: Варианта 500$ берса 15
# ой 100$ дан беришга" -- "Варианта" ("как вариант"/"один из вариантов")
# прямо перед суммой значит "это ОДИН ИЗ вариантов оплаты" (в данном случае
# сама схема рассрочки: 500 сейчас, затем 15 месяцев по 100), а не твёрдая
# цена машины. "Нархи:" в начале строки не помогает -- FULL_PRICE_LABEL_RE
# ищет метку строго перед числом (LABEL_WINDOW), а между "Нархи:" и "500$"
# стоит "Варианта", так что метка и не должна была сработать. Проблема была
# в другом: одинокая непомеченная сумма (500) резолвилась как уверенная
# full_price, хотя рядом с ней стоит явный маркер "это вариант оплаты".
VARIANT_RE = re.compile(r"вариант\w*|variantda|variant\b", re.I)
VARIANT_MAX_DISTANCE = 20

# То же отрицание, что и в regex_extract.py FLAG_PATTERNS -- если рядом с
# маркером стоит "нет/не/yo'q/йўқ", маркер не считается.
NEGATION_RE = re.compile(
    r"\bне\s|\bбез\s|uchramagan|bo'?lmagan|bulmagan|emas\b|yo'?q\b|siz\b"
    r"|йўқ|йук", re.I,
)
NEGATION_WINDOW = 20

# Метка "Narxi:"/"Нархи:" ищется совсем рядом (перед числом) -- это самый
# точный, локальный сигнал, поэтому окно для неё узкое и однонаправленное.
LABEL_WINDOW = 15


def _magnitude_multiplier(word: str | None) -> int:
    if not word:
        return 1
    return _MAGNITUDE.get(word.lower(), 1)


def _to_float(raw: str) -> float:
    digits = re.sub(r"[^\d]", "", raw)
    return float(digits) if digits else 0.0


@dataclass
class MoneyMention:
    value: float
    currency: str  # "usd" | "uzs"
    price_type: str  # full_price_labeled | down_payment | monthly_payment | exchange_addition | candidate
    start: int
    end: int
    raw: str


@dataclass
class PriceResult:
    price_usd: float | None = None
    price_uzs: float | None = None
    price_type: str = "unknown"
    price_confidence: str = "low"  # high | medium | low
    needs_review: bool = True
    price_reason: str = "no_money_found"
    mentions: list = field(default_factory=list)


def _all_raw_matches(text: str):
    """Все совпадения MONEY_USD_RE/MONEY_UZS_RE вперемешку, отсортированные
    по позиции -- нужно, чтобы построить контекстное окно, ОГРАНИЧЕННОЕ
    соседними денежными упоминаниями (см. _classify ниже)."""
    spans = []
    for m in MONEY_USD_RE.finditer(text):
        raw = m.group(1) or m.group(2)
        spans.append((m.start(), m.end(), _to_float(raw), "usd"))
    for m in MONEY_UZS_RE.finditer(text):
        raw, mag_word = m.group(1), m.group(2)
        value = _to_float(raw) * _magnitude_multiplier(mag_word)
        spans.append((m.start(), m.end(), value, "uzs"))
    spans.sort(key=lambda s: s[0])
    return spans


# Максимальное расстояние (в символах) от маркера до числа, на которое
# маркер ещё может влиять. Дальше -- считаем, что это про другую сумму
# или вообще не про деньги в этом предложении.
MAX_MARKER_DISTANCE = 40

# Порядок здесь = приоритет: если у одного числа несколько маркеров
# конкурируют (редко, но возможно), выигрывает более ранний в списке.
# Третий элемент -- своё максимальное расстояние (см. MONTHLY_BARE_RE).
_CONTEXT_PATTERNS = [
    ("down_payment", DOWN_PAYMENT_RE, MAX_MARKER_DISTANCE),
    ("monthly_payment", MONTHLY_RE, MAX_MARKER_DISTANCE),
    ("monthly_payment", MONTHLY_BARE_RE, MONTHLY_BARE_MAX_DISTANCE),
    ("exchange_addition", EXCHANGE_RE, MAX_MARKER_DISTANCE),
    ("uncertain_variant", VARIANT_RE, VARIANT_MAX_DISTANCE),
]


def _distance(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    if a_end <= b_start:
        return b_start - a_end
    if b_end <= a_start:
        return a_start - b_end
    return 0  # пересекаются


def find_money_mentions(text: str) -> list[MoneyMention]:
    """Находит ВСЕ денежные упоминания в тексте (не только первое) и
    классифицирует контекст каждого по отдельности.

    Раньше контекстное окно вырезалось ПО СЕРЕДИНЕ между соседними числами
    -- но если маркер (например "первоначальный взнос", 14+ символов)
    физически длиннее половины расстояния между числами, середина обрезает
    сам маркер, и он не распознаётся вообще ни для одного числа. Поэтому
    вместо "вырезать окно и искать в нём" -- ищем маркеры ПО ВСЕМУ тексту
    и привязываем каждое совпадение к БЛИЖАЙШЕМУ числу (по расстоянию в
    символах, не длиннее MAX_MARKER_DISTANCE)."""
    norm = normalize_text(text)
    spans = _all_raw_matches(norm)
    if not spans:
        return []

    types = ["candidate"] * len(spans)

    # 1) метка "Narxi:"/"Нархи:" -- ищем только НЕПОСРЕДСТВЕННО перед
    #    числом (локальный, самый надёжный сигнал), выигрывает у всего.
    for idx, (start, end, value, currency) in enumerate(spans):
        label_window = norm[max(0, start - LABEL_WINDOW):start]
        if FULL_PRICE_LABEL_RE.search(label_window):
            types[idx] = "full_price_labeled"

    # 2) остальные маркеры -- глобально по тексту, каждое совпадение
    #    привязывается к ближайшему числу (если оно не дальше, чем
    #    max_distance для этого паттерна, и число ещё не full_price_labeled).
    for ptype, pattern, max_distance in _CONTEXT_PATTERNS:
        for m in pattern.finditer(norm):
            neg_window = norm[max(0, m.start() - NEGATION_WINDOW): m.end() + NEGATION_WINDOW]
            if NEGATION_RE.search(neg_window):
                continue
            best_idx, best_dist = None, None
            for idx, (start, end, value, currency) in enumerate(spans):
                if types[idx] == "full_price_labeled":
                    continue
                d = _distance(m.start(), m.end(), start, end)
                if d > max_distance:
                    continue
                if best_dist is None or d < best_dist:
                    best_idx, best_dist = idx, d
            if best_idx is not None and types[best_idx] == "candidate":
                types[best_idx] = ptype

    return [
        MoneyMention(value, currency, types[idx], start, end, norm[start:end])
        for idx, (start, end, value, currency) in enumerate(spans)
    ]


def resolve_price(text: str) -> PriceResult:
    """Главная функция: текст объявления -> PriceResult с price_usd/uzs,
    price_type, confidence, needs_review и причиной решения.

    Правило, которое нельзя нарушать: down_payment/monthly_payment/
    exchange_addition НИКОГДА не попадают в price_usd/price_uzs, даже если
    это единственное найденное число."""
    norm = normalize_text(text)
    mentions = find_money_mentions(text)

    if not mentions:
        # "Нархи: келишамиз" без единого числа -- цена явно и осознанно
        # договорная, это не то же самое, что "не смогли понять текст".
        if NEGOTIABLE_RE.search(norm):
            return PriceResult(price_type="negotiable", price_confidence="high",
                                needs_review=False, price_reason="negotiable_no_number",
                                mentions=[])
        return PriceResult(price_reason="no_money_found", mentions=[])

    full_candidates = [mm for mm in mentions if mm.price_type in ("full_price_labeled", "candidate")]
    non_full = [mm for mm in mentions if mm.price_type not in ("full_price_labeled", "candidate")]
    labeled = [mm for mm in full_candidates if mm.price_type == "full_price_labeled"]

    usd_full = [mm for mm in full_candidates if mm.currency == "usd"]
    uzs_full = [mm for mm in full_candidates if mm.currency == "uzs"]
    # если есть хоть одна помеченная "Narxi:"-сумма, безымянные "candidate"
    # того же типа не должны путать резолюцию -- отдаём приоритет labeled.
    if any(mm.price_type == "full_price_labeled" for mm in usd_full):
        usd_full = [mm for mm in usd_full if mm.price_type == "full_price_labeled"]
    if any(mm.price_type == "full_price_labeled" for mm in uzs_full):
        uzs_full = [mm for mm in uzs_full if mm.price_type == "full_price_labeled"]

    def resolve_currency(cands: list[MoneyMention]) -> tuple[float | None, str]:
        distinct_values = {round(c.value) for c in cands}
        if len(distinct_values) == 0:
            return None, "none"
        if len(distinct_values) == 1:
            return cands[0].value, "single"
        return None, "ambiguous"

    usd_val, usd_state = resolve_currency(usd_full)
    uzs_val, uzs_state = resolve_currency(uzs_full)

    # Неоднозначность в ОДНОЙ валюте обесценивает и "чистый" результат в
    # другой -- если пост называет доллары "$12,000 ... $16,500" вперемешку
    # с кредитом, спокойная одна сумма в сумах рядом почти наверняка тоже
    # часть той же неоднозначной схемы (ежемесячный платёж и т.п.), а не
    # независимая честная полная цена. Лучше needs_review, чем угадать.
    if usd_state == "ambiguous" or uzs_state == "ambiguous":
        return PriceResult(price_type="unknown", price_confidence="low",
                            needs_review=True, price_reason="multiple_full_price_candidates",
                            mentions=mentions)

    if usd_val is not None or uzs_val is not None:
        confidence = "high" if labeled else "medium"
        return PriceResult(
            price_usd=usd_val, price_uzs=uzs_val, price_type="full_price",
            price_confidence=confidence, needs_review=False,
            price_reason="labeled_price" if labeled else "single_candidate_price",
            mentions=mentions,
        )

    # Нет ни одного full_price-кандидата -- цена явно НЕ определена, но у нас
    # есть контекст (down_payment/monthly/exchange), полезный для владельца
    # объявления, чтобы решить руками. price_usd/uzs остаются None -- это и
    # есть требование "первый взнос никогда не становится полной ценой".
    if non_full:
        types_found = {mm.price_type for mm in non_full}
        # uncertain_variant само по себе не описывает ЧТО это за платёж
        # (первый взнос/ежемесячный/etc) -- это просто "не доверяй этой
        # сумме", поэтому не годится как выбираемый chosen_type сам по себе.
        labelable_types = types_found - {"uncertain_variant"}
        if "down_payment" in labelable_types and "monthly_payment" in labelable_types:
            chosen_type = "installment"
        elif labelable_types:
            chosen_type = sorted(labelable_types)[0]
        else:
            chosen_type = "unknown"
        return PriceResult(price_type=chosen_type, price_confidence="medium",
                            needs_review=True, price_reason="only_partial_price_found",
                            mentions=mentions)

    return PriceResult(price_reason="no_money_found", mentions=mentions)
