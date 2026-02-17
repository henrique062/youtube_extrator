# Deploy no EasyPanel

## 1) Build
- Fonte: repositório deste projeto
- Build: automático pelo `Dockerfile`

## 2) Serviço Web (Flask/Gunicorn)
- Criar serviço com a imagem do projeto
- Variáveis de ambiente:
  - `APP_MODE=web`
  - `PORT=5000`
  - `GUNICORN_WORKERS=2`
  - `GUNICORN_THREADS=4`
  - `GUNICORN_TIMEOUT=180`
- Porta interna do container: `5000`
- Volume recomendado:
  - Host: diretório persistente
  - Container: `/app/downloads`

## 3) Serviço Bot do Telegram (opcional)
- Criar outro serviço com a mesma imagem
- Variáveis de ambiente:
  - `APP_MODE=telegram`
  - `TELEGRAM_BOT_TOKEN=SEU_TOKEN`
- Volume recomendado:
  - Host: diretório persistente
  - Container: `/app/downloads`

## 4) Entrypoint
- O container usa `docker-entrypoint.sh`
- Modos disponíveis:
  - `web`: sobe `gunicorn` com `server:app`
  - `telegram`: sobe `python telegram_bot.py`
  - `cli`: sobe `python youtube_tool.py`
