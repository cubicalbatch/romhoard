#!/bin/bash
set -e

# Wait for database to be ready
wait_for_db() {
    echo "Waiting for database..."
    while ! python -c "
import os
import psycopg
conn = psycopg.connect(
    dbname=os.environ.get('POSTGRES_DB', 'romhoard'),
    user=os.environ.get('POSTGRES_USER', 'romhoard'),
    password=os.environ.get('POSTGRES_PASSWORD', ''),
    host=os.environ.get('POSTGRES_HOST', 'localhost'),
    port=os.environ.get('POSTGRES_PORT', '5432'),
)
conn.close()
" 2>/dev/null; do
        sleep 1
    done
    echo "Database is ready!"
}

case "$1" in
    all)
        wait_for_db
        echo "Running migrations..."
        python manage.py migrate --no-input
        echo "Syncing system definitions..."
        python manage.py sync_systems
        echo "Syncing device presets..."
        python manage.py sync_presets
        echo "Starting supervisord..."
        exec supervisord -c /etc/supervisor/conf.d/romhoard.conf
        ;;
    web)
        wait_for_db
        echo "Running migrations..."
        python manage.py migrate --no-input
        echo "Syncing system definitions..."
        python manage.py sync_systems
        echo "Syncing device presets..."
        python manage.py sync_presets
        echo "Starting Gunicorn server..."
        exec gunicorn romhoard.wsgi:application \
            --bind 0.0.0.0:6766 \
            --workers ${GUNICORN_WORKERS:-2} \
            --threads ${GUNICORN_THREADS:-4} \
            --access-logfile - \
            --error-logfile -
        ;;
    worker)
        wait_for_db
        echo "Starting Procrastinate worker..."
        exec python manage.py worker \
            --queues=${WORKER_QUEUES:-user_actions,background,metadata} \
            --concurrency=${WORKER_CONCURRENCY:-4}
        ;;
    migrate)
        wait_for_db
        echo "Running migrations..."
        exec python manage.py migrate --no-input
        ;;
    collectstatic)
        echo "Collecting static files..."
        exec python manage.py collectstatic --no-input
        ;;
    shell)
        wait_for_db
        exec python manage.py shell
        ;;
    *)
        # Pass through any other command
        exec "$@"
        ;;
esac
