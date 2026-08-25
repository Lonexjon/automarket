"""
Шаг 2 авторизации Telethon. Завершает вход по коду, полученному на шаге 1.
Использование: python telegram_auth_step2.py <телефон> <код> <phone_code_hash>
phone_code_hash -- то, что напечатал telegram_auth_step1.py при отправке кода.
"""
import asyncio
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

load_dotenv()

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION_PATH = os.path.join(os.path.dirname(__file__), "..", "automarket_session")


def _proxy_from_env():
    raw = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not raw:
        return None
    p = urlparse(raw)
    return ("http", p.hostname, p.port)


async def main(phone: str, code: str, phone_code_hash: str):
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH, proxy=_proxy_from_env())
    await client.connect()
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        print("На аккаунте включена двухфакторная аутентификация (облачный пароль).")
        pw = input("Введи пароль 2FA: ")
        await client.sign_in(password=pw)

    me = await client.get_me()
    print(f"Успешно вошли как: {me.first_name} (id={me.id}, phone={me.phone})")
    print(f"Файл сессии сохранён: {SESSION_PATH}.session")
    await client.disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Использование: python telegram_auth_step2.py +998901234567 12345 <phone_code_hash>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3]))
