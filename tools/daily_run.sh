#!/usr/bin/env bash
# Ежедневный цикл сбора: свежие посты из Telegram -> разбор -> дедуп -> deal_score.
# Каждый шаг идемпотентен (ON CONFLICT / повторный запуск ничего не портит),
# так что безопасно гонять по расписанию (см. cron.txt рядом).
set -euo pipefail
cd "$(dirname "$0")/.."

source .venv/bin/activate

echo "=== $(date -u +%FT%TZ) telegram_ingest ==="
python3 parsers/telegram_ingest.py 200

echo "=== $(date -u +%FT%TZ) regex_extract ==="
python3 parsers/regex_extract.py

echo "=== $(date -u +%FT%TZ) dedupe ==="
python3 tools/dedupe.py

echo "=== $(date -u +%FT%TZ) deal_score ==="
python3 tools/deal_score.py

echo "=== $(date -u +%FT%TZ) done ==="
