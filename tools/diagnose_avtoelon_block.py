"""
Диагностика: почему Playwright с сервера видит "Страница не найдена" на
объявлении, которое в обычном браузере грузится нормально. Гипотеза --
IP сервера (датацентровый) блокируется на уровне API-запросов сайта
(HTML-каркас отдаётся всем, а XHR/fetch за данными объявления -- нет),
аналогично тому, что уже видели с OLX.uz.

Перехватывает все сетевые ответы при заходе на страницу и печатает
статус-коды -- ищем 403/429/пустые тела у запросов, похожих на API.

Использование:
  python3 tools/diagnose_avtoelon_block.py
"""
import asyncio

URL = "https://avtoelon.uz/a/show/7490939"


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )
        )

        responses = []

        def on_response(resp):
            responses.append((resp.status, resp.request.resource_type, resp.url))

        page.on("response", on_response)

        await page.goto(URL, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(1000)

        NOISE = ("analytics", "yandex.ru", "kcdn.online", "google", "gdpr", "mc.yandex")

        print(f"Всего сетевых ответов: {len(responses)}\n")
        for status, rtype, url in responses:
            if any(n in url for n in NOISE):
                continue  # аналитика/трекинг -- не относится к делу
            marker = "  <-- ПОДОЗРИТЕЛЬНО" if status >= 400 else ""
            if rtype in ("xhr", "fetch", "document") or status >= 400:
                print(f"{status} [{rtype}] {url}{marker}")

        print("\n--- title сейчас на странице ---")
        print(await page.title())

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
