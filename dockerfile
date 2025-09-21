FROM python:3.12-slim

WORKDIR /app

# Системные зависимости (psycopg нужен для alembic; asyncpg — для приложения)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем проект
COPY . .

# Переменные окружения: вывод Python без буферизации
ENV PYTHONUNBUFFERED=1

CMD bash -lc "alembic -c bot/db_repo/migrations/alembic.ini upgrade head && python bot/app.py"