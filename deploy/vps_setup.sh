#!/bin/bash
# Первичная настройка VPS для automarket. Запускать по одной команде из этого
# файла через веб-консоль Vultr (root), не одним куском -- проще ловить ошибки.
set -e

# 1. Обновление системы и базовые пакеты
apt-get update && apt-get upgrade -y
apt-get install -y python3 python3-venv python3-pip git curl ufw

# 2. Базовый firewall -- открываем только то, что реально нужно
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# 3. Склонировать проект
cd /root
git clone https://github.com/Lonexjon/automarket.git
cd automarket

# 4. Venv и зависимости (как в dev-песочнице)
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install telethon python-dotenv "python-socks[asyncio]"
playwright install --with-deps chromium

echo "DONE: base setup complete"
