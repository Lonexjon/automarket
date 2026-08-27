"""
Разовый сброс: обнуляет photo_urls у всех telegram-объявлений, чтобы
fetch_telegram_photos.py прогнал их заново с исправленной вырезкой блока
нужного поста (раньше могло подцепляться фото соседнего сообщения на
превью-странице -- см. коммит с extract_message_block).

Использование:
  python3 tools/reset_photo_urls.py
  python3 tools/fetch_telegram_photos.py   # пересобрать заново
"""
import os
import sqlite3

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")

con = sqlite3.connect(DB_PATH)
cur = con.execute("UPDATE listings SET photo_urls = NULL WHERE source = 'telegram' AND photo_urls IS NOT NULL")
con.commit()
print(f"Сброшено: {cur.rowcount}")
con.close()
