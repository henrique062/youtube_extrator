#!/bin/sh
set -eu

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
