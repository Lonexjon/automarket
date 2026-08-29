"""
Разовый (но безопасно перезапускаемый) пересчёт price_usd/price_uzs/
price_type/price_confidence/needs_review/price_reason для УЖЕ вставленных
объявлений -- нужен один раз после того, как money.py/regex_extract.py
поменяли правила классификации цены. regex_extract.py сам эти поля
проставляет только при первой вставке строки, существующие объявления не
трогает (как и с флагами -- см. tools/reflag_listings.py, тот же паттерн).

Это ИМЕННО тот шаг, который чинит уже накопленные ошибки на проде (первый
взнос/ежемесячный платёж, который раньше осел в price_usd как будто это
цена машины) -- без него исправление в money.py действует только на новые
объявления, а старые продолжают показывать неверную цену/deal_score.

Безопасно перезапускать сколько угодно раз (идемпотентно: на чистых данных
второй прогон ничего не меняет). Не трогает объявления без description_raw.

Использование:
  python3 tools/reprocess_prices.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
import money  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")


def main():
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        """SELECT id, description_raw, price_usd, price_uzs, price_type, price_confidence, needs_review, price_reason
           FROM listings WHERE description_raw IS NOT NULL"""
    ).fetchall()

    changed = 0
    price_freed = 0  # раньше был price_usd/uzs, теперь корректно NULL -- это и есть основной фикс
    for (listing_id, text, old_usd, old_uzs, old_type,
         old_conf, old_review, old_reason) in rows:
        r = money.resolve_price(text)
        new_review = int(r.needs_review)

        old_tuple = (old_usd, old_uzs, old_type, old_conf, old_review, old_reason)
        new_tuple = (r.price_usd, r.price_uzs, r.price_type, r.price_confidence, new_review, r.price_reason)
        if old_tuple == new_tuple:
            continue

        if (old_usd or old_uzs) and not (r.price_usd or r.price_uzs):
            price_freed += 1

        con.execute(
            """UPDATE listings SET price_usd=?, price_uzs=?, price_type=?,
               price_confidence=?, needs_review=?, price_reason=? WHERE id=?""",
            (r.price_usd, r.price_uzs, r.price_type, r.price_confidence, new_review, r.price_reason, listing_id),
        )
        changed += 1

    con.commit()
    con.close()
    print(f"Проверено: {len(rows)}")
    print(f"Изменилось: {changed}")
    print(f"Из них: была цена -> стала NULL (первый взнос/платёж и т.п., пойманы задним числом): {price_freed}")


if __name__ == "__main__":
    main()
