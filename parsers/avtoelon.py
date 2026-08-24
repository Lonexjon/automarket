"""
Парсер Avtoelon.uz.

Сайт — client-rendered SPA (Vue): сервер отдаёт пустой HTML-каркас на
любой прямой запрос без исполнения JS (проверено — main-error.js бандл
на /a/show/<id> даже с куками и полным набором браузерных заголовков).
Поэтому карточки рендерим через Playwright, а не httpx/selectolax.

Обнаружение объявлений — через sitemap.xml, а не через листинг с
фильтрами (тот тоже client-rendered): в sitemap лежат прямые ссылки на
все активные объявления, это надёжнее и не требует раскрутки пагинации.

ВАЖНО: рендеринг через Playwright не протестирован вживую — в текущей
среде разработки исходящий трафик Chromium идёт через локальный прокси
сессии, который сбрасывает соединение независимо от целевого хоста (то
же самое на example.com), это инфраструктурная особенность песочницы
Claude Code, а не блокировка со стороны Avtoelon. На реальном сервере
такого прокси не будет. Перед первым запуском в проде — прогнать
`selftest()` и свериться вручную с несколькими объявлениями на сайте.
"""

from __future__ import annotations

import asyncio
import gzip
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


async def fetch_rendered_html(playwright_page, url: str) -> str:
    """Рендерит карточку объявления в headless-браузере и возвращает готовый HTML."""
    await playwright_page.goto(url, wait_until="networkidle", timeout=30_000)
    await playwright_page.wait_for_timeout(500)  # добить поздний JS (цена иногда рендерится с задержкой)
    return await playwright_page.content()


def parse_advert_html(html: str, source_url: str) -> RawListing | None:
    """
    Разбирает уже отрендеренный HTML карточки объявления.

    Селекторы намеренно не финализированы — их нужно сверить с реальным
    рендером на проде (см. selftest ниже), здесь — заготовка по разумным
    предположениям об общей структуре Vue-приложения объявлений (h1 для
    заголовка, блок с ценой, значения атрибутов в виде label/value пар).
    """
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    source_id = ADVERT_URL_RE.search(source_url).group(1)

    listing = RawListing(source_id=source_id, source_url=source_url)

    title_node = tree.css_first("h1")
    if title_node:
        listing.title = title_node.text(strip=True)

    price_node = tree.css_first('[class*="price"]')
    if price_node:
        price_text = price_node.text(strip=True)
        digits = re.sub(r"[^\d]", "", price_text)
        if digits:
            value = float(digits)
            if "y.e" in price_text.lower() or "$" in price_text:
                listing.price_usd = value
            else:
                listing.price_uzs = value
        listing.currency_raw = price_text

    photos = tree.css('img[src*="kluz-photos"]')
    listing.photo_urls = list({img.attributes.get("src", "") for img in photos if img.attributes.get("src")})

    desc_node = tree.css_first('[class*="description"]')
    if desc_node:
        listing.description_raw = desc_node.text(strip=True)

    if not listing.title and not listing.price_usd and not listing.price_uzs:
        return None  # похоже, страница не отрендерилась/это error-заглушка

    return listing


async def selftest(sample_size: int = 5) -> list[RawListing]:
    """
    Прогоняет sample_size объявлений end-to-end (sitemap -> Playwright -> parse)
    и печатает результат для ручной сверки с сайтом. Запускать перед первым
    боевым проходом парсера и после любых правок parse_advert_html.
    """
    from playwright.async_api import async_playwright

    async with httpx.AsyncClient(timeout=30) as client:
        urls = await discover_advert_urls(client, limit=sample_size)

    results: list[RawListing] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(user_agent=USER_AGENT)
        for url in urls:
            html = await fetch_rendered_html(page, url)
            listing = parse_advert_html(html, url)
            if listing:
                results.append(listing)
                print(f"OK  {url} -> {listing.title!r} price_usd={listing.price_usd} price_uzs={listing.price_uzs}")
            else:
                print(f"FAIL {url} -> не удалось распарсить (проверить селекторы / рендер)")
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
        await browser.close()

    return results


if __name__ == "__main__":
    asyncio.run(selftest())
