# ==============================================================================
# TeslaCam ExportDash Container
# Base: Debian Bookworm Slim with Node.js 20 & FFmpeg & Cairo/Canvas dependencies
# ==============================================================================

FROM node:20-bookworm-slim

LABEL maintainer="Antigravity TeslaCam Orchestrator" \
      description="Automated Tesla dashcam viewer, telemetry extractor, and video export service"

ENV DEBIAN_FRONTEND=noninteractive \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1

# Install system dependencies for node-canvas, font rendering, native modules, and ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libcairo2-dev \
    libpango1.0-dev \
    libjpeg-dev \
    libgif-dev \
    librsvg2-dev \
    build-essential \
    python3 \
    git \
    ca-certificates \
    curl \
    fonts-freefont-ttf \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /app

# Create mount points for TeslaCam source and rendered exports
RUN mkdir -p /data/input /data/output

# Copy package manifests first for optimal layer caching
COPY package.json package-lock.json* ./

# Install npm dependencies
RUN npm ci

# Copy application source code
COPY . .

# Copy and configure entrypoint & CLI utilities
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY tesla-stitch.sh /usr/local/bin/tesla-stitch
RUN chmod +x /usr/local/bin/docker-entrypoint.sh /usr/local/bin/tesla-stitch

# Pre-build Next.js application
RUN npm run build || true

# Declare ports and persistent mount volumes
EXPOSE 3000
VOLUME ["/data/input", "/data/output"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["npm", "run", "dev", "--", "-H", "0.0.0.0", "-p", "3000"]
