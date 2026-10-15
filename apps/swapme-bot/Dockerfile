FROM python:3.11-slim

# Часовой пояс: ежедневный отчёт в 21:00 и статистика «за день» считаются
# по локальному времени — на хостинге без этого будет UTC
ENV TZ=Europe/Prague
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# База данных хранится на постоянном диске (volume), путь задаётся через DB_PATH
ENV DB_PATH=/data/swapme.db

CMD ["python", "bot.py"]
