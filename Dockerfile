FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apt-get purge -y gcc libc-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY app/ .

RUN mkdir -p /app/screenshots

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--loop", "uvloop"]
