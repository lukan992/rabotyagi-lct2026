FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# ffmpeg берёт кадры из видео на анализ и крутит демо-ролики
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
# uv — из PyPI, откуда и так ставятся зависимости (образ uv с ghcr.io бывает недоступен)
RUN pip install --no-cache-dir "uv>=0.9,<0.10"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY models ./models
COPY app ./app

EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/api/meta', timeout=4)"]
# Один воркер: конвейер видео (кадры на анализ, сверка) живёт внутри процесса и не должен дублироваться
# --ws-max-size: сообщения браузера в /api/tracks — килобайты; 16 МБ по умолчанию не нужны
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--ws-max-size", "1048576"]
