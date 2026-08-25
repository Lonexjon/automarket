"""
Считает deal_score для каждого объявления: насколько цена ниже/выше медианы
своего сегмента (brand + year).

Модель почти нигде не извлечена (regex её не выделяет надёжно), поэтому
сегмент пока строим по марка+год -- это грубее, чем марка+модель+год, но
единственный вариант, пока модель массово не заполнена.

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
import os
import sqlite3
import statistics
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")
SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")


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
        """SELECT id, brand, year, price_usd FROM listings
           WHERE duplicate_of IS NULL AND brand IS NOT NULL AND year IS NOT NULL
             AND removed_at IS NULL"""
    ).fetchall()

    segment_prices = defaultdict(list)
    for _, brand, year, price_usd in rows:
        if price_usd:
            segment_prices[(brand, year)].append(price_usd)

    medians = {
        segment: statistics.median(prices)
        for segment, prices in segment_prices.items()
    }

    updated = 0
    for listing_id, brand, year, price_usd in rows:
        segment = (brand, year)
        median = medians.get(segment)
        sample_size = len(segment_prices.get(segment, []))

        if median is None or not price_usd:
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
    print(f"Сегментов (brand+year): {len(medians)}")
    print(f"Объявлений обновлено: {updated}")


if __name__ == "__main__":
    main()
