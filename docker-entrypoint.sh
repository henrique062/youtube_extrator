#!/bin/sh
set -eu

# Atualizar yt-dlp para a versão mais recente (evita erros de formato do YouTube)
echo "Atualizando yt-dlp..."
pip install --upgrade yt-dlp 2>/dev/null || echo "Aviso: não foi possível atualizar yt-dlp"

MODE="${APP_MODE:-web}"

case "$MODE" in
  web)
    exec gunicorn \
      --bind "0.0.0.0:${PORT:-5000}" \
      --workers "${GUNICORN_WORKERS:-2}" \
      --threads "${GUNICORN_THREADS:-4}" \
      --timeout "${GUNICORN_TIMEOUT:-180}" \
      server:app
    ;;
  telegram)
    exec python telegram_bot.py
    ;;
  cli)
    exec python youtube_tool.py
    ;;
  *)
    echo "APP_MODE invalido: $MODE"
    echo "Use: web | telegram | cli"
    exit 1
    ;;
esac
