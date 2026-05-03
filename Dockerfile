FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# SQLite lives on a mounted volume in production
ENV DB_PATH=/data/lms.db
ENV PORT=8080

# Create data dir (will be replaced by mounted volume on Fly.io / Render)
RUN mkdir -p /data

EXPOSE 8080

# gunicorn for production. 2 workers handle a small group fine.
CMD ["gunicorn", "-w", "2", "-b", "0.0.0.0:8080", "--access-logfile", "-", "app:app"]
