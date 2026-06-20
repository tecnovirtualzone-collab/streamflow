FROM python:3.11-slim

# Instalar Node.js, Chromium y FFmpeg
RUN apt-get update && apt-get install -y \
    curl gnupg \
    chromium \
    ffmpeg \
    libgbm-dev libasound2 libatk1.0-0 libcairo2 \
    libcups2 libdbus-1-3 libglib2.0-0 libgtk-3-0 \
    libnspr4 libnss3 libpango-1.0-0 libx11-6 \
    libxcomposite1 libxdamage1 libxext6 libxrandr2 \
    fonts-liberation \
    --no-install-recommends \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir --force-reinstall -r requirements.txt && \
    python3 -c "import psycopg2; print('psycopg2 OK')"

# Node deps
COPY wa-service/package*.json ./wa-service/
RUN cd wa-service && npm install --production

COPY . .

EXPOSE 5000 3001

COPY start.sh .
RUN chmod +x start.sh
CMD ["./start.sh"]
