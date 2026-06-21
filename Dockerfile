FROM node:20-alpine

# Install FFmpeg for streaming
RUN apk add --no-cache ffmpeg

WORKDIR /app

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm install --production

# Copy application code
COPY src ./src
COPY public ./public
COPY server.js ./

# Environment variables (all configurable at runtime)
ENV PORT=5000
ENV DATA_DIR=/data
ENV JWT_SECRET=streamflow-change-me
ENV JWT_EXPIRES_IN=24h
ENV BCRYPT_ROUNDS=10
ENV INITIAL_ADMIN_PASSWORD=admin123
ENV STREAM_MAX_CONCURRENT=3
ENV STREAM_INACTIVITY_TIMEOUT_MS=120000
ENV TZ=America/Bogota
ENV NODE_ENV=production

# Create data directory
RUN mkdir -p /data

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:5000/health || exit 1

# Start
CMD ["node", "server.js"]
