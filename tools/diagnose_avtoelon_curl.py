"""
Последняя проверка перед выводом "нужны прокси": простой httpx-запрос
(без Playwright вообще) на ту же страницу с несколькими вариантами
заголовков -- чтобы исключить, что дело в чём-то специфичном для
Playwright/Chromium, а не в блокировке IP как таковой.

Использование:
  python3 tools/diagnose_avtoelon_curl.py
"""
import asyncio

import httpx

URL = "https://avtoelon.uz/a/show/7490939"

HEADER_SETS = {
    "минимальные": {},
    "как настоящий браузер": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    },
}


async def main():
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for label, headers in HEADER_SETS.items():
            try:
                resp = await client.get(URL, headers=headers)
                print(f"{label}: status={resp.status_code} bytes={len(resp.content)}")
                print(f"  первые 200 символов: {resp.text[:200]!r}\n")
            except Exception as e:
                print(f"{label}: ОШИБКА -- {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
