FROM python:3.11-slim

# ── System deps ───────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    gnupg \
    xvfb \
    x11vnc \
    fluxbox \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# ── Python deps ───────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Install Playwright & browsers ─────────────────────────────
RUN playwright install chromium
RUN playwright install-deps chromium

# ── Copy app code ─────────────────────────────────────────────
COPY . .

# Create session/screenshot dirs
RUN mkdir -p /app/sessions /app/screenshots

# Startup: launch Xvfb display then FastAPI
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
