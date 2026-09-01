"""
Парсер OLX.uz.

В отличие от Avtoelon (client-rendered Vue-SPA), OLX отдаёт готовый
HTML с данными объявления прямо в разметке -- Playwright не нужен,
хватает httpx + selectolax. Подтверждено на реальной странице
(https://www.olx.uz/d/obyavlenie/.../ID....html, сохранённой и
разобранной вручную 2026-08-31).

Селекторы построены на `data-testid` -- OLX (как большинство React/Next
SPA) держит их стабильнее, чем сгенерированные `css-XXXXXX` классы,
которые меняются от билда к билду.

ВАЖНО (см. tools/diagnose_avtoelon_block.py и docs/PROJECT_OVERVIEW.md):
с IP дата-центра (VPS) OLX отдаёт 403 на голый HTTP-запрос -- подтверждено
и через VPS, и через песочницу разработки. С домашнего IP владельца
403 тоже был на голый httpx-запрос, но полноценный браузер (Playwright)
проходит нормально после недолгого прогрева (первый запрос может словить
таймаут на antibot-проверке, повторный -- проходит чисто). Поэтому здесь
тоже используем Playwright для самого запроса страницы, а не голый httpx,
хотя парсинг результата -- обычный HTML, без ожидания дополнительного JS
кроме начальной отрисовки.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

BASE_URL = "https://www.olx.uz"
CATEGORY_URL = f"{BASE_URL}/transport/legkovye-avtomobili/"
LISTING_URL_RE = re.compile(r"/d/obyavlenie/[^\"'\s]+\.html")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

REQUEST_DELAY_SECONDS = 1.0  # OLX построже Avtoelon -- не торопимся


@dataclass
class RawListing:
    source: str = "olx"
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
    attrs: dict = field(default_factory=dict)  # brand, model, year, mileage_km, transmission, ...


async def fetch_rendered_html(playwright_page, url: str) -> str:
    await playwright_page.goto(url, wait_until="networkidle", timeout=30_000)
    await playwright_page.wait_for_timeout(500)
    return await playwright_page.content()


async def discover_listing_urls(playwright_page, category_url: str = CATEGORY_URL, limit: int | None = None) -> list[str]:
    """Ссылки на объявления с одной страницы категории (без пагинации --
    для регулярного сбора этого достаточно, каталог обновляется чаще, чем
    успевает устареть первая страница; пагинацию добавить отдельно, если
    понадобится глубже одной страницы)."""
    html = await fetch_rendered_html(playwright_page, category_url)
    urls = []
    seen = set()
    for m in LISTING_URL_RE.finditer(html):
        url = urljoin(BASE_URL, m.group(0))
        if url not in seen:
            seen.add(url)
            urls.append(url)
        if limit and len(urls) >= limit:
            break
    return urls


# "Модель: Lacetti / Gentra", "Год выпуска: 2020 ", "Пробег: 155 000 км" --
# каждый параметр в ad-parameters-container это <p>Метка: значение</p>.
# Ключи -- то, что реально видели в разметке, не выдуманы заранее.
PARAM_LABEL_MAP = {
    "модель": "model_raw",
    "год выпуска": "year_raw",
    "пробег": "mileage_raw",
    "коробка передач": "transmission_raw",
    "цвет": "color_raw",
    "вид топлива": "fuel_raw",
    "состояние машины": "condition_raw",
    "тип кузова": "body_raw",
    "условия продажи": "sale_terms_raw",
}


def parse_listing_html(html: str, source_url: str) -> RawListing | None:
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    listing = RawListing(source_url=source_url)

    # Реальная разметка вставляет HTML-комментарий между меткой "ID:" и
    # самими цифрами (см. дамп страницы: 'ID: <!-- -->65458744') -- прямой
    # regex "ID:\s*(\d+)" по этой причине никогда не совпадает, \s* не
    # матчит узел-комментарий. Не боремся с этим -- URL-слаг объявления
    # (последний "-ID....html" сегмент) сам по себе стабильный и уникальный
    # на объявление, этого достаточно для source_id.
    listing.source_id = source_url.rstrip("/").rsplit("-", 1)[-1].replace(".html", "")

    title_node = tree.css_first('[data-testid="offer_title"] h4')
    if title_node:
        listing.title = title_node.text(strip=True)

    price_node = tree.css_first('[data-testid="ad-price-container"] h3')
    if price_node:
        price_text = price_node.text(strip=True)
        listing.currency_raw = price_text
        digits = re.sub(r"[^\d]", "", price_text)
        if digits:
            value = float(digits)
            # OLX пишет либо "у.е." (условные единицы = доллары на практике
            # на этом рынке), либо "сум" -- явных других валют не видели.
            if "у.е" in price_text.lower() or "$" in price_text:
                listing.price_usd = value
            else:
                listing.price_uzs = value

    negotiable_node = tree.css_first('[data-testid="ad-price-container"] p')
    if negotiable_node and "договорн" in negotiable_node.text(strip=True).lower():
        listing.attrs["negotiable"] = True

    for p in tree.css('[data-testid="ad-parameters-container"] p'):
        text = p.text(strip=True)
        if ":" not in text:
            continue
        label, _, value = text.partition(":")
        key = PARAM_LABEL_MAP.get(label.strip().lower())
        if key:
            listing.attrs[key] = value.strip()

    desc_node = tree.css_first('[data-testid="ad_description"] div')
    if desc_node:
        listing.description_raw = desc_node.text(strip=True)

    # Первый <p> внутри map-aside-section -- заголовок секции ("Местоположение"),
    # сам город -- следующий <p> за ним (см. дамп реальной страницы).
    location_ps = [
        p.text(strip=True) for p in tree.css('[data-testid="map-aside-section"] p')
        if p.text(strip=True) and p.text(strip=True) != "Местоположение"
    ]
    if location_ps:
        listing.city = location_ps[0].rstrip(",").strip()

    posted_node = tree.css_first('[data-testid="ad-posted-at"]')
    if posted_node:
        listing.posted_at = posted_node.text(strip=True).replace("Опубликовано", "").strip()

    photos = tree.css('[data-testid="ad-photo"] img')
    listing.photo_urls = list({img.attributes.get("src", "") for img in photos if img.attributes.get("src")})

    if not listing.title and listing.price_usd is None and listing.price_uzs is None:
        return None  # похоже, страница не отрендерилась/это заглушка

    return listing


async def selftest(sample_size: int = 5) -> list[RawListing]:
    """Прогоняет sample_size объявлений с первой страницы категории
    (discover -> render -> parse) и печатает результат для ручной сверки.
    Запускать перед первым боевым проходом и после любых правок парсера."""
    from playwright.async_api import async_playwright

    results: list[RawListing] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=USER_AGENT)

        urls = await discover_listing_urls(page, limit=sample_size)
        for url in urls:
            html = await fetch_rendered_html(page, url)
            listing = parse_listing_html(html, url)
            if listing:
                results.append(listing)
                print(
                    f"OK  {url} -> {listing.title!r} "
                    f"price_usd={listing.price_usd} price_uzs={listing.price_uzs} city={listing.city!r}"
                )
            else:
                print(f"FAIL {url} -> не удалось распарсить (проверить селекторы / рендер)")
            await asyncio.sleep(REQUEST_DELAY_SECONDS)
        await browser.close()

    return results


if __name__ == "__main__":
    asyncio.run(selftest())
