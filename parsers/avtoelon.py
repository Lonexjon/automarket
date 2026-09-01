"""
Парсер Avtoelon.uz.

ИСПРАВЛЕНО 2026-09-01: раньше здесь считалось, что сайт -- client-rendered
Vue-SPA и сервер отдаёт пустой HTML-каркас (см. историю). Это было
предположение, сделанное из-за того, что тестовый запрос из песочницы
разработки не проходил (прокси сессии сбрасывал соединение независимо от
целевого хоста, включая example.com -- это была особенность песочницы, не
факт о сайте). На реальной странице объявления (сохранённой вручную с
домашнего IP владельца и разобранной здесь) выяснилось, что Avtoelon
рендерит HTML на сервере, данные объявления лежат прямо в разметке
(schema.org itemprop + простые CSS-классы вроде `a-price__text`,
`description-params`). Playwright для парсинга не нужен -- обычный
httpx + selectolax, как и для Telegram-пайплайна.

С IP дата-центра (VPS) сайт всё равно недоступен (403/сброс соединения) --
см. tools/diagnose_avtoelon_block.py и docs/PROJECT_OVERVIEW.md. С
домашнего IP владельца -- открывается нормально, обычным HTTP-запросом,
без браузера вообще.
"""

from __future__ import annotations

import asyncio
import gzip
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

BASE_URL = "https://avtoelon.uz"
SITEMAP_INDEX = f"{BASE_URL}/sitemap.xml"
ADVERT_URL_RE = re.compile(r"https://avtoelon\.uz/a/show/(\d+)")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

REQUEST_DELAY_SECONDS = 0.7  # не долбим сайт быстрее ~1.5 req/s


@dataclass
class RawListing:
    source: str = "avtoelon"
    source_id: str = ""
    source_url: str = ""
    title: str = ""
    price_usd: float | None = None
    price_uzs: float | None = None
    currency_raw: str = ""
    city: str = ""
    description_raw: str = ""
    photo_urls: list[str] = field(default_factory=list)
    posted_at: str | None = None
    attrs: dict = field(default_factory=dict)  # brand, model, year, mileage_km, ...


async def discover_advert_urls(client: httpx.AsyncClient, limit: int | None = None) -> list[str]:
    """Собирает ссылки на объявления из sitemap-index. limit — для отладки/дымового теста."""
    resp = await client.get(SITEMAP_INDEX, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    sub_sitemaps = re.findall(r"<loc>(https://avtoelon\.uz/sitemap/sitemap-adverts\.\d+\.xml\.gz)</loc>", resp.text)

    seen: set[str] = set()
    urls: list[str] = []
    for sm_url in sub_sitemaps:
        resp = await client.get(sm_url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        xml_text = gzip.decompress(resp.content).decode("utf-8")
        # каждая запись <url> несёт несколько <xhtml:link> (ru/uz/mobile alternate)
        # на тот же самый /a/show/<id>, поэтому дедуп обязателен.
        for m in ADVERT_URL_RE.finditer(xml_text):
            url = m.group(0)
            if url not in seen:
                seen.add(url)
                urls.append(url)
        if limit and len(urls) >= limit:
            return urls[:limit]
    return urls


# "Год" / "Пробег" / "Коробка передач" / ... -> <dt class="value-title">Метка</dt>
# <dd class="value clearfix">Значение</dd>. Ключи -- то, что реально видели в
# разметке (см. docstring выше), не выдуманы заранее.
PARAM_LABEL_MAP = {
    "город": "city_raw",
    "год": "year_raw",
    "пробег": "mileage_raw",
    "коробка передач": "transmission_raw",
    "цвет": "color_raw",
    "объем двигателя, л": "engine_volume_raw",
    "состояние краски": "paint_condition_raw",
    "привод": "drive_raw",
    "торг есть": "negotiable_raw",
}


def parse_advert_html(html: str, source_url: str) -> RawListing | None:
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    id_m = ADVERT_URL_RE.search(source_url)
    source_id = id_m.group(1) if id_m else ""

    listing = RawListing(source_id=source_id, source_url=source_url)

    brand_node = tree.css_first('h1.a-title__text [itemprop="brand"]')
    name_node = tree.css_first('h1.a-title__text [itemprop="name"]')
    title_parts = [n.text(strip=True) for n in (brand_node, name_node) if n and n.text(strip=True)]
    listing.title = " ".join(title_parts)

    price_node = tree.css_first(".a-price .a-price__text")
    if price_node:
        price_text = price_node.text(strip=True)
        listing.currency_raw = price_text
        digits = re.sub(r"[^\d]", "", price_text)
        if digits:
            value = float(digits)
            low = price_text.lower()
            if "y.e" in low or "у.е" in low or "$" in price_text:
                listing.price_usd = value
            else:
                listing.price_uzs = value

    dts = tree.css("dl.description-params dt.value-title")
    for dt in dts:
        label = dt.text(strip=True).lower()
        key = PARAM_LABEL_MAP.get(label)
        if not key:
            continue
        dd = dt.next
        while dd is not None and dd.tag != "dd":
            dd = dd.next
        if dd is not None:
            listing.attrs[key] = dd.text(strip=True)

    if "city_raw" in listing.attrs:
        listing.city = listing.attrs.pop("city_raw")

    # Не у каждого объявления есть свободный текст от продавца (это
    # объявление, на котором строился парсер, -- пример без него, только
    # структурные параметры). Пробуем несколько вероятных мест, ничего не
    # придумываем -- если не нашли, description_raw остаётся пустым, а не
    # заполняется угадыванием.
    desc_node = tree.css_first(".a-comment, .comment__text, .item-description, [itemprop='description']")
    if desc_node:
        text = desc_node.text(strip=True)
        # itemprop="description" на этой странице -- это обёртка ВСЕГО блока
        # параметров (dl), не свободный текст; отфильтровываем такой случай.
        if text and "description-params" not in (desc_node.html or ""):
            listing.description_raw = text

    photos = tree.css("ul.photo-list img")
    photo_urls = []
    for img in photos:
        src = img.attributes.get("src", "")
        if src:
            # маленькие превью (60x45) -- берём то же имя файла с полным
            # разрешением, паттерн виден в самой ссылке (-full.webp у линков
            # <a class="small-thumb" href="...-full.webp">).
            photo_urls.append(src)
    full_links = tree.css("a.small-thumb")
    if full_links:
        photo_urls = [a.attributes.get("href", "") for a in full_links if a.attributes.get("href")]
    listing.photo_urls = list(dict.fromkeys(p for p in photo_urls if p))

    if not listing.title and listing.price_usd is None and listing.price_uzs is None:
        return None  # похоже, страница не отрендерилась/это error-заглушка

    return listing


async def fetch_advert_html(client: httpx.AsyncClient, url: str) -> str:
    resp = await client.get(url, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text


async def selftest(sample_size: int = 5) -> list[RawListing]:
    """Прогоняет sample_size объявлений end-to-end (sitemap -> httpx -> parse)
    и печатает результат для ручной сверки с сайтом. Запускать перед первым
    боевым проходом парсера и после любых правок parse_advert_html."""
    results: list[RawListing] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        urls = await discover_advert_urls(client, limit=sample_size)
        for url in urls:
            html = await fetch_advert_html(client, url)
            listing = parse_advert_html(html, url)
            if listing:
                results.append(listing)
                print(f"OK  {url} -> {listing.title!r} price_usd={listing.price_usd} price_uzs={listing.price_uzs} city={listing.city!r}")
            else:
                print(f"FAIL {url} -> не удалось распарсить (проверить селекторы)")
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

    return results


if __name__ == "__main__":
    asyncio.run(selftest())
