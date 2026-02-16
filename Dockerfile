# =============================================================================
# Stage 1: Tailwind CSS Builder - Compile CSS from templates
# =============================================================================
FROM node:20-slim AS tailwind-builder

WORKDIR /app

# Copy package files and install dependencies
COPY package.json ./
RUN npm install

# Copy Tailwind config and source files
COPY tailwind.config.js ./
COPY static/ static/
COPY templates/ templates/
COPY library/templates/ library/templates/
COPY romcollections/templates/ romcollections/templates/
COPY devices/templates/ devices/templates/

# Build minified Tailwind CSS
RUN npx tailwindcss -i static/css/tailwind-input.css -o static/css/tailwind.css --minify

# =============================================================================
# Stage 2: Python Builder - Install dependencies and compile wheels
# =============================================================================
FROM python:3.13-slim-bookworm AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libjpeg-dev \
    libpng-dev \
    libfreetype6-dev \
    zlib1g-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for better caching
COPY pyproject.toml uv.lock ./

# Sync production dependencies only (no dev deps)
RUN uv sync --frozen --no-dev --no-editable

# =============================================================================
# Stage 3: Runtime - Minimal production image
# =============================================================================
FROM python:3.13-slim-bookworm

# Install runtime dependencies only (no dev headers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    libpng16-16 \
    libfreetype6 \
    libpq5 \
    supervisor \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash romhoard

WORKDIR /app

# Copy virtual environment from builder (readable by all)
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY . .

# Copy built Tailwind CSS from tailwind-builder stage
COPY --from=tailwind-builder /app/static/css/tailwind.css /app/static/css/tailwind.css

# Copy and setup entrypoint
COPY --chmod=755 docker/docker-entrypoint.sh /usr/local/bin/

# Copy supervisord config
COPY docker/supervisord.conf /etc/supervisor/conf.d/romhoard.conf

# Collect static files and fix permissions
RUN PATH="/app/.venv/bin:$PATH" python manage.py collectstatic --no-input && \
    chmod -R a+rX /app && \
    mkdir -p /app/data /app/data/metadata && chown -R romhoard:romhoard /app/data

# Note: Running as root so supervisord can drop privileges per-process
# Each supervised process runs as 'romhoard' user (configured in supervisord.conf)

# Add virtual environment to PATH and set defaults
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Database defaults (matches docker-compose.prod.yml)
    POSTGRES_DB=romhoard \
    POSTGRES_USER=romhoard \
    POSTGRES_PASSWORD=romhoard_password \
    POSTGRES_HOST=db \
    POSTGRES_PORT=5432 \
    # Application defaults
    ROM_LIBRARY_ROOT=/roms \
    IMAGE_STORAGE_PATH=/app/data/metadata \
    DEBUG=false \
    # Use bundled assets instead of CDN for offline support
    USE_BUNDLED_ASSETS=true \
    # Supervisor process defaults
    GUNICORN_WORKERS=2 \
    GUNICORN_THREADS=4 \
    WORKER_USER_CONCURRENCY=1 \
    WORKER_BG_CONCURRENCY=8

# Expose web server port
EXPOSE 6766

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["all"]
