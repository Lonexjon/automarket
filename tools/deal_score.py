"""
Считает deal_score для каждого объявления: насколько цена ниже/выше медианы
своего сегмента (brand + model + year). Если модель не распозналась (None) --
это тоже валидная группа сама по себе, просто грубее.

Считаем медиану только по объявлениям, чья цена -- УВЕРЕННО полная цена
машины:
  - каноническим (duplicate_of IS NULL) -- репост не должен задвоенно
    давить на медиану;
  - price_type == 'full_price' (см. parsers/money.py) -- первый взнос,
    ежемесячный платёж, доплата к обмену и т.п. никогда не попадают ни в
    медиану, ни в собственный deal_score объявления. Это жёсткое правило,
    не эвристика "если похоже на рассрочку" -- price_type уже решил это
    на этапе разбора текста;
  - needs_review == 0 -- неоднозначная цена (несколько разных "похожих на
    полную" сумм в тексте) не участвует, пока не расчищена руками.

deal_score = (median - price) / median * 100, округлено до 1 знака.
Положительное число = объявление дешевле рынка (хорошая цена),
отрицательное = дороже рынка.

Сегменты с < MIN_SEGMENT_SIZE объявлениями НЕ получают deal_score вообще
(раньше получали -- segment_sample_size показывался как предупреждение, но
цифра скидки всё равно выводилась, что на практике никто не читает мелкий
принт под ней). Один-два объявления в сегменте -- это не рынок, это
рандом; deal_score = None честнее, чем ложная уверенность.

Использование:
  python3 tools/deal_score.py
"""
import json
import os
import sqlite3
import statistics
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")
SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")

# Ниже этого цена почти наверняка зацепила не то число, даже если
# price_type='full_price' -- легковушка в Узбекистане дешевле не продаётся.
# Абсолютный порог ловит только самые грубые случаи, дальше есть ещё
# относительный фильтр (OUTLIER_RATIO).
MIN_PLAUSIBLE_PRICE_USD = 1000

# Если цена меньше этой доли от медианы своего сегмента -- она статистически
# подозрительна независимо от суммы (скорее всего regex зацепил не то число).
# Такую цену не учитываем в медиане и не даём ей deal_score -- честно
# показываем "не знаем", а не подсовываем вводящий в заблуждение % скидки.
OUTLIER_RATIO = 0.35

# Меньше этого объявлений в сегменте -- медиана статистически ненадёжна,
# deal_score не считаем вообще (не показываем "выгодно"/"дорого" на основе
# одного-двух случайных объявлений).
MIN_SEGMENT_SIZE = 3

FULL_PRICE_TYPE = "full_price"


def is_trustworthy_price(price_usd: float | None, price_type: str | None, needs_review: int | None) -> bool:
    """Цена, которую можно уверенно считать полной стоимостью машины --
    единственное место, где решается "участвует эта цена в медиане/своём
    deal_score или нет". price_type/needs_review приходят из money.py
    (parsers/regex_extract.py на этапе разбора) -- никогда не пересчитываем
    их здесь заново по ключевым словам, чтобы не разойтись с тем, что
    реально хранится в базе."""
    return bool(price_usd) and price_type == FULL_PRICE_TYPE and not needs_review


def ensure_schema(con: sqlite3.Connection) -> None:
    for col, decl in [
        ("deal_score", "REAL"),
        ("segment_median_usd", "REAL"),
        ("segment_sample_size", "INTEGER"),
    ]:
        try:
            con.execute(f"ALTER TABLE listings ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass  # уже есть, или таблицы ещё нет (создастся ниже)
    con.commit()

    with open(SCHEMA_PATH) as f:
        con.executescript(f.read())
    con.commit()


def main():
    con = sqlite3.connect(DB_PATH)
    ensure_schema(con)

    # Без фильтра по duplicate_of/brand/year/removed_at в самом SELECT --
    # иначе дубли и объявления без марки/года никогда не попадают в цикл
    # обновления ниже и сохраняют СТАРЫЙ deal_score/медиану, оставшиеся от
    # прошлого прогона (реальный случай на проде: дубль с price_type=
    # down_payment так и держал deal_score=0.0 с прошлой схемы, потому что
    # раньше сюда вообще не попадал -- health_check был прав, что это
    # нарушение инварианта, просто чинить нужно было тут). Ограничение по
    # каноничности/наличию brand+year по-прежнему действует, но теперь
    # только там, где оно принципиально: при построении медиан сегмента.
    rows = con.execute(
        """SELECT id, brand, model, year, price_usd, price_type, needs_review, duplicate_of, removed_at
           FROM listings"""
    ).fetchall()

    def is_canonical(duplicate_of, removed_at, brand, year) -> bool:
        return duplicate_of is None and removed_at is None and brand is not None and year is not None

    # Первый проход: грубая медиана по сегменту, только чтобы отсеять
    # относительные выбросы -- сама по себе она объявлениям не присваивается.
    raw_prices = defaultdict(list)
    for _, brand, model, year, price_usd, price_type, needs_review, duplicate_of, removed_at in rows:
        if (
            is_canonical(duplicate_of, removed_at, brand, year)
            and is_trustworthy_price(price_usd, price_type, needs_review)
            and price_usd >= MIN_PLAUSIBLE_PRICE_USD
        ):
            raw_prices[(brand, model, year)].append(price_usd)
    raw_medians = {
        segment: statistics.median(prices) for segment, prices in raw_prices.items()
    }

    # Второй проход: убираем цены дальше OUTLIER_RATIO от грубой медианы,
    # медиана на чистых данных -- она и попадает в базу как segment_median_usd.
    segment_prices = defaultdict(list)
    for _, brand, model, year, price_usd, price_type, needs_review, duplicate_of, removed_at in rows:
        segment = (brand, model, year)
        raw_median = raw_medians.get(segment)
        if (
            is_canonical(duplicate_of, removed_at, brand, year)
            and is_trustworthy_price(price_usd, price_type, needs_review)
            and price_usd >= MIN_PLAUSIBLE_PRICE_USD
            and raw_median
            and price_usd >= raw_median * OUTLIER_RATIO
        ):
            segment_prices[segment].append(price_usd)

    medians = {
        segment: statistics.median(prices)
        for segment, prices in segment_prices.items()
        if len(prices) >= MIN_SEGMENT_SIZE
    }

    updated = 0
    trusted_prices = 0
    for listing_id, brand, model, year, price_usd, price_type, needs_review, duplicate_of, removed_at in rows:
        segment = (brand, model, year)
        median = medians.get(segment) if is_canonical(duplicate_of, removed_at, brand, year) else None
        sample_size = len(segment_prices.get(segment, [])) if median is not None else 0
        trustworthy = is_canonical(duplicate_of, removed_at, brand, year) and is_trustworthy_price(
            price_usd, price_type, needs_review
        )
        if trustworthy:
            trusted_prices += 1
        is_outlier = not trustworthy or not median or price_usd < median * OUTLIER_RATIO

        if median is None or not trustworthy or is_outlier:
            deal_score = None
        else:
            deal_score = round((median - price_usd) / median * 100, 1)

        con.execute(
            """UPDATE listings SET deal_score = ?, segment_median_usd = ?, segment_sample_size = ?
               WHERE id = ?""",
            (deal_score, median, sample_size, listing_id),
        )
        updated += 1

    con.commit()
    con.close()
    print(f"Сегментов (brand+model+year) с медианой (>= {MIN_SEGMENT_SIZE} объявлений): {len(medians)}")
    print(f"Объявлений обновлено: {updated}")
    print(f"Из них с доверенной (full_price, не needs_review) ценой: {trusted_prices}")


if __name__ == "__main__":
    main()
