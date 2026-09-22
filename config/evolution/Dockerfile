# Build once while online; all evaluation containers run offline and without credentials.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest==8.3.5
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 HOME=/tmp
USER 65534:65534
WORKDIR /src
