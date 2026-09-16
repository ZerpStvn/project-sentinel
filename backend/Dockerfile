FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# Shell form so $PORT (injected by PaaS platforms like Railway) is honored;
# falls back to 8000 for local `docker compose`, which sets an explicit
# command anyway and is unaffected.
CMD ["sh", "-c", "daphne -b 0.0.0.0 -p ${PORT:-8000} sentinel.asgi:application"]
