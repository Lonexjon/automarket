"""
Шаг 1 авторизации Telethon. Запросить у Телеграма код подтверждения.
Номер телефона передаётся аргументом (в международном формате, +998...).
Код придёт в само приложение Телеграм (или SMS, если приложение недоступно)
на этот номер -- сообщи мне код, я завершу авторизацию (шаг 2).
"""
import asyncio
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION_PATH = os.path.join(os.path.dirname(__file__), "..", "automarket_session")


def _proxy_from_env():
    """
    Прямой TCP до серверов Telegram (MTProto) в этой dev-песочнице висит --
    выходной трафик тут разрешён только через локальный HTTP(S)-прокси сессии
    (тот же, что использует curl через переменную HTTPS_PROXY). Заворачиваем
    туда и Telethon через python-socks. На реальном сервере в проде этого
    прокси не будет -- там просто не передавать proxy=... в TelegramClient.
    """
    raw = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not raw:
        return None
    p = urlparse(raw)
    return ("http", p.hostname, p.port)


async def main(phone: str):
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH, proxy=_proxy_from_env())
    await client.connect()
    sent = await client.send_code_request(phone)
    print(f"Код отправлен на {phone}. phone_code_hash={sent.phone_code_hash}")
    # phone_code_hash не обязателен для telethon sign_in (он сам подхватит его из
    # сессии клиента), печатаем на всякий случай для отладки.
    await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Использование: python telegram_auth_step1.py +998901234567")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
