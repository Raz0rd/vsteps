FROM python:3.12-slim

WORKDIR /app

# ca-certificates pra instalar cert mitmproxy em runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

# Deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App
COPY . .

# Data dir
RUN mkdir -p /app/data/logs

# Expose dashboard
EXPOSE 5050

# Default: roda dashboard + worker via script
CMD ["python", "start.py"]
