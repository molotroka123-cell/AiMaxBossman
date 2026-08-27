# Bossman Core: API + раннер агентов + раннер проектов.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ffmpeg docker.io && \
    rm -rf /var/lib/apt/lists/*
# docker.io — только клиент: run/tests запускают sandbox-контейнер без сети
# через смонтированный /var/run/docker.sock (см. compose.core.yaml).

WORKDIR /app
COPY pyproject.toml ./
COPY bossman ./bossman
COPY db ./db
COPY agents ./agents
COPY tools ./tools
COPY ui ./ui
RUN pip install --no-cache-dir .

ENV BOSSMAN_ROOT=/app
EXPOSE 8700
CMD ["bossman", "serve"]
