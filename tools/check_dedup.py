"""
Разовая проверка масштаба дублей по phone_hash перед тем, как писать
логику дедупликации. Дублем считаем только полное совпадение телефона
(один и тот же продавец может честно продавать несколько разных машин
с одного номера -- это не дубли).

Использование:
  python3 tools/check_dedup.py
"""
import os
import sqlite3

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")

con = sqlite3.connect(DB_PATH)

total_with_phone = con.execute(
    "SELECT COUNT(*) FROM listings WHERE length(phone_hash) > 0"
).fetchone()[0]

dupe_groups = con.execute(
    """SELECT phone_hash, COUNT(*) c FROM listings
       WHERE length(phone_hash) > 0
       GROUP BY phone_hash HAVING c > 1
       ORDER BY c DESC"""
).fetchall()

print(f"Всего объявлений с телефоном: {total_with_phone}")
print(f"Уникальных телефонов с 2+ объявлениями: {len(dupe_groups)}\n")

print("Топ-5 телефонов по числу объявлений (сам номер не показываем, только hash):")
for phone_hash, count in dupe_groups[:5]:
    print(f"\n--- hash {phone_hash[:10]}... -- {count} объявлений ---")
    rows = con.execute(
        """SELECT brand, year, price_usd, mileage_km, source_url FROM listings
           WHERE phone_hash = ? ORDER BY posted_at""",
        (phone_hash,),
    ).fetchall()
    for brand, year, price, mileage, url in rows:
        print(f"  {brand} {year} -- ${price} -- {mileage} km -- {url}")

con.close()
