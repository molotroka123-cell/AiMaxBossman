# Sandbox для run/tests: инструменты разработки, БЕЗ сети (--network none при запуске).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        git make gcc curl jq && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir pytest
WORKDIR /work
