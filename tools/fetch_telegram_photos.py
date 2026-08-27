"""
Достаёт прямые ссылки на фото объявлений из ПУБЛИЧНОЙ HTML-превью страницы
Telegram (t.me/s/<channel>/<message_id>) -- без скачивания и хранения фото
у себя. Это те же правила, что и для OLX/Avtoelon: photo_url ведёт на CDN
самого источника (в данном случае -- серверы Telegram), мы не re-host'им.

t.me/s/... -- открытая страница без авторизации, специально предназначена
Telegram для встраивания превью постов на сторонних сайтах.

Фото у поста ищем как background-image:url('...') внутри
tgme_widget_message_photo_wrap -- так Telegram верстает превью-страницу.
Если разметка Telegram сменится, эти два маркера придётся поправить.

ВАЖНО: t.me/s/<channel>/<id> отдаёт не только запрошенный пост, а несколько
соседних сообщений для контекста -- если брать первое найденное фото по
всей странице, легко подцепить фото ЧУЖОГО поста. Поэтому сначала находим
блок именно нужного сообщения (по data-post="channel/id", так Telegram
маркирует каждый пост в разметке), и ищем фото только внутри него.

Использование:
  python3 tools/fetch_telegram_photos.py            # все необработанные
  python3 tools/fetch_telegram_photos.py 50         # ограничить (тест)
"""
import asyncio
import json
import os
import re
import sqlite3
import sys

import httpx

ROOT = os.path.join(os.path.dirname(__file__), "..")
DB_PATH = os.path.join(ROOT, "automarket.db")

PHOTO_URL_RE = re.compile(
    r'tgme_widget_message_photo_wrap["\'\s][^>]*?background-image:\s*url\([\'"]([^\'"]+)[\'"]\)',
    re.I,
)

REQUEST_DELAY_SECONDS = 1.0
MAX_PHOTOS_PER_LISTING = 5


def fetch_unprocessed(con: sqlite3.Connection, limit: int | None):
    query = """
        SELECT l.id, l.source_id FROM listings l
        JOIN telegram_raw r ON r.channel || ':' || r.message_id = l.source_id
        WHERE l.source = 'telegram' AND l.photo_urls IS NULL AND r.has_photo = 1
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    return con.execute(query).fetchall()


def extract_message_block(html: str, channel: str, message_id: str) -> str | None:
    """Вырезает HTML именно нужного поста -- страница отдаёт несколько
    соседних сообщений, каждое помечено data-post="channel/id"."""
    marker = f'data-post="{channel}/{message_id}"'
    start = html.find(marker)
    if start == -1:
        return None
    # блок сообщения -- от его начала (немного назад, до открывающего тега
    # div) до начала СЛЕДУЮЩЕГО тега с data-post -- следующий пост уже не наш.
    block_start = html.rfind("<div", 0, start)
    next_marker_pos = html.find('data-post="', start + len(marker))
    block_end = html.rfind("<div", 0, next_marker_pos) if next_marker_pos != -1 else len(html)
    return html[block_start if block_start != -1 else start:block_end]


async def fetch_photo_urls(client: httpx.AsyncClient, channel: str, message_id: str) -> list[str]:
    resp = await client.get(f"https://t.me/s/{channel}/{message_id}")
    if resp.status_code != 200:
        return []

    block = extract_message_block(resp.text, channel, message_id)
    if block is None:
        return []  # не нашли разметку именно этого поста -- лучше пусто, чем чужое фото

    urls = PHOTO_URL_RE.findall(block)
    # дедуп с сохранением порядка
    seen: set[str] = set()
    result = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
        if len(result) >= MAX_PHOTOS_PER_LISTING:
            break
    return result


async def main(limit: int | None):
    con = sqlite3.connect(DB_PATH)
    rows = fetch_unprocessed(con, limit)
    print(f"К обработке: {len(rows)} объявлений с фото\n")

    found, empty = 0, 0
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for listing_id, source_id in rows:
            channel, message_id = source_id.split(":", 1)
            try:
                urls = await fetch_photo_urls(client, channel, message_id)
            except Exception as e:
                print(f"FAIL {listing_id} ({source_id}): {e}")
                urls = []

            if urls:
                con.execute(
                    "UPDATE listings SET photo_urls = ? WHERE id = ?",
                    (json.dumps(urls), listing_id),
                )
                found += 1
            else:
                empty += 1
            con.commit()
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    con.close()
    print(f"Найдены фото: {found}")
    print(f"Не нашлось (превью пустое/сменилась разметка): {empty}")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(main(limit))
