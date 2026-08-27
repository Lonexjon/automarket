"""
Считает deal_score для каждого объявления: насколько цена ниже/выше медианы
своего сегмента (brand + model + year). Если модель не распозналась (None) --
это тоже валидная группа сама по себе, просто грубее.

Считаем медиану только по:
  - каноническим объявлениям (duplicate_of IS NULL) -- репост не должен
    задвоенно давить на медиану;
  - объявлениям с ценой в $ (price_usd) -- сумовые без курса конвертации
    в выборку медианы не берём, но deal_score им всё равно посчитаем,
    если у сегмента есть медиана от других объявлений.

deal_score = (median - price) / median * 100, округлено до 1 знака.
Положительное число = объявление дешевле рынка (хорошая цена),
отрицательное = дороже рынка.

Сегменты с < MIN_SAMPLE объявлениями всё равно получают deal_score, но
segment_sample_size честно показывает, что доверять ему рано -- решает
это на фронтенде.

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

# Ниже этого regex почти наверняка зацепил не цену машины, а число из
# текста про рассрочку/предоплату/что-то ещё -- легковушка в Узбекистане
# дешевле не продаётся. Абсолютный порог ловит только самые грубые случаи
# ("$100" вместо цены) -- он никогда не покроет всё (рассрочка тоже может
# начинаться от $1500), поэтому дальше есть ещё относительный фильтр.
MIN_PLAUSIBLE_PRICE_USD = 1000

# Если цена меньше этой доли от медианы своего сегмента -- она статистически
# подозрительна независимо от суммы (скорее всего regex зацепил не то число).
# Такую цену не учитываем в медиане и не даём ей deal_score -- честно
# показываем "не знаем", а не подсовываем вводящий в заблуждение % скидки.
OUTLIER_RATIO = 0.35

# Объявление с этим флагом (см. regex_extract.py FLAG_PATTERNS) -- цена в
# нём почти наверняка первый взнос по рассрочке, а не полная стоимость.
# Такую цену тоже не учитываем в медиане и не даём ей deal_score --
# иначе рассрочка систематически выглядит как "супер-выгодная цена".
INSTALLMENT_FLAG_CODE = "installment_price_mentioned"


def has_installment_flag(flags_raw: str | None) -> bool:
    if not flags_raw:
        return False
    try:
        flags = json.loads(flags_raw)
    except (json.JSONDecodeError, TypeError):
        return False
    return any(f.get("code") == INSTALLMENT_FLAG_CODE for f in flags)


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

    rows = con.execute(
        """SELECT id, brand, model, year, price_usd, flags FROM listings
           WHERE duplicate_of IS NULL AND brand IS NOT NULL AND year IS NOT NULL
             AND removed_at IS NULL"""
    ).fetchall()

    # Первый проход: грубая медиана по сегменту, только чтобы отсеять
    # относительные выбросы -- сама по себе она объявлениям не присваивается.
    raw_prices = defaultdict(list)
    for _, brand, model, year, price_usd, flags_raw in rows:
        if price_usd and price_usd >= MIN_PLAUSIBLE_PRICE_USD and not has_installment_flag(flags_raw):
            raw_prices[(brand, model, year)].append(price_usd)
    raw_medians = {
        segment: statistics.median(prices) for segment, prices in raw_prices.items()
    }

    # Второй проход: убираем цены дальше OUTLIER_RATIO от грубой медианы,
    # медиана на чистых данных -- она и попадает в базу как segment_median_usd.
    segment_prices = defaultdict(list)
    for _, brand, model, year, price_usd, flags_raw in rows:
        segment = (brand, model, year)
        raw_median = raw_medians.get(segment)
        if (
            price_usd
            and price_usd >= MIN_PLAUSIBLE_PRICE_USD
            and not has_installment_flag(flags_raw)
            and raw_median
            and price_usd >= raw_median * OUTLIER_RATIO
        ):
            segment_prices[segment].append(price_usd)

    medians = {
        segment: statistics.median(prices)
        for segment, prices in segment_prices.items()
    }

    updated = 0
    for listing_id, brand, model, year, price_usd, flags_raw in rows:
        segment = (brand, model, year)
        median = medians.get(segment)
        sample_size = len(segment_prices.get(segment, []))
        is_outlier = not price_usd or not median or price_usd < median * OUTLIER_RATIO

        if (
            median is None
            or not price_usd
            or price_usd < MIN_PLAUSIBLE_PRICE_USD
            or has_installment_flag(flags_raw)
            or is_outlier
        ):
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
    print(f"Сегментов (brand+model+year): {len(medians)}")
    print(f"Объявлений обновлено: {updated}")


if __name__ == "__main__":
    main()
