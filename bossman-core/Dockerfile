# Bossman Core: API + раннер агентов + раннер проектов.
# Build context = REPOSITORY ROOT (the image also ships the bossman-shared
# distribution: cache observation contract, learning trace store, JSON schemas):
#   docker build -f bossman-core/Dockerfile -t bossman-core .
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        git ffmpeg docker.io && \
    rm -rf /var/lib/apt/lists/*
# docker.io — только клиент: run/tests запускают sandbox-контейнер без сети
# через смонтированный /var/run/docker.sock (см. compose.core.yaml).

WORKDIR /app
# shared contracts first (separate layer; changes rarely)
COPY pyproject.toml /src/shared/pyproject.toml
COPY bossman_shared /src/shared/bossman_shared
COPY learning /src/shared/learning
COPY schemas /src/shared/schemas
RUN pip install --no-cache-dir /src/shared && rm -rf /src/shared

COPY bossman-core/pyproject.toml ./
COPY bossman-core/bossman ./bossman
COPY bossman-core/db ./db
COPY bossman-core/agents ./agents
COPY bossman-core/tools ./tools
COPY bossman-core/ui ./ui
RUN pip install --no-cache-dir .
# the installed app must resolve the shared contracts without the checkout
RUN cd / && python -c "import bossman_shared.cache_observation, learning.trace, bossman_schemas; import bossman._shared as s; assert s.AVAILABLE"

ENV BOSSMAN_ROOT=/app
EXPOSE 8700
CMD ["bossman", "serve"]
