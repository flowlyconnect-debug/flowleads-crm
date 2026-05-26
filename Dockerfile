# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home appuser

COPY --from=builder /install /usr/local
COPY . .

RUN mkdir -p /app/backups /app/uploads \
    && chown -R appuser:appuser /app

ENV FLASK_APP=run.py
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

USER appuser

CMD ["gunicorn", "run:app", "--workers", "4", "--bind", "0.0.0.0:8000"]
