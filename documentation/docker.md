# Docker Deployment

This guide covers deploying RomHoard using Docker.

## Quick Start

```bash
mkdir roms
docker compose up -d
```

Access the web interface at http://localhost:6766

That's it! A secure secret key is automatically generated on first run and persisted in the data volume.

## Environment Variables

All environment variables have sensible defaults. You only need to set them if you want to customize behavior.

### Optional Variables

Override these in your `.env` file or docker-compose to customize behavior:

#### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_SECRET_KEY` | *(auto-generated)* | Django secret key. Auto-generated and persisted on first run. Set this to use your own key. |

#### Web Server

| Variable | Default | Description |
|----------|---------|-------------|
| `GUNICORN_WORKERS` | `2` | Number of Gunicorn worker processes |
| `GUNICORN_THREADS` | `4` | Number of threads per worker |

#### Background Worker

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKER_QUEUES` | `user_actions,background,metadata` | Comma-separated list of queues to process |
| `WORKER_CONCURRENCY` | `4` | Number of concurrent task workers |

#### ScreenScraper Integration

| Variable | Default | Description |
|----------|---------|-------------|
| `SCREENSCRAPER_USER` | *(empty)* | ScreenScraper username (can also be set via UI) |
| `SCREENSCRAPER_PASSWORD` | *(empty)* | ScreenScraper password (can also be set via UI) |

#### Database

These are pre-configured for the docker-compose setup. Only override if using an external database:

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_DB` | `romhoard` | Database name |
| `POSTGRES_USER` | `romhoard` | Database user |
| `POSTGRES_PASSWORD` | `romhoard_password` | Database password |
| `POSTGRES_HOST` | `db` | Database host |
| `POSTGRES_PORT` | `5432` | Database port |

#### Application Paths

These are pre-configured in the Docker image:

| Variable | Default | Description |
|----------|---------|-------------|
| `ROM_LIBRARY_ROOT` | `/roms` | Path to ROM library inside container |
| `IMAGE_STORAGE_PATH` | `/app/data/metadata` | Path to metadata storage inside container |
| `DEBUG` | `false` | Enable Django debug mode (never in production) |

## Custom Docker Compose

For advanced setups, create a `docker-compose.override.yml`:

```yaml
services:
  romhoard:
    ports:
      - "9000:6766"  # Change external port
    volumes:
      - /mnt/storage/roms:/roms
    environment:
      GUNICORN_WORKERS: 4
      GUNICORN_THREADS: 8
      SCREENSCRAPER_USER: myuser
      SCREENSCRAPER_PASSWORD: mypassword
```

Then run with:

```bash
docker compose up -d
```

## Volume Mounts

| Container Path | Purpose |
|----------------|---------|
| `/roms` | Your ROM library (read-only access is sufficient) |
| `/app/data` | Application data (includes metadata storage at `/app/data/metadata`) |

## Scaling Workers

For large libraries, you may want to run multiple workers:

```yaml
# docker-compose.override.yml
services:
  worker:
    deploy:
      replicas: 2
    environment:
      WORKER_CONCURRENCY: 4
```

## Health Checks

The database container includes a health check. The web and worker services wait for the database to be healthy before starting.

To check container health:

```bash
docker compose ps
```

## Logs

View logs for all services:

```bash
docker compose logs -f
```

View logs for a specific service:

```bash
docker compose logs -f romhoard
```

## Updating

To update to a new version:

```bash
docker compose pull
docker compose up -d
```

## Backup

### Database

```bash
docker compose exec db pg_dump -U romhoard romhoard > backup.sql
```

### Restore

```bash
docker compose exec -T db psql -U romhoard romhoard < backup.sql
```
