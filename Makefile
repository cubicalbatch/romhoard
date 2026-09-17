.PHONY: dev dev-proxy workers workers-proxy proxy

dev:
	bash -c 'source .env && mkdir -p data/logs && LOG_FILE=$$PWD/data/logs/server.log uv run python manage.py migrate && uv run python manage.py runserver 0.0.0.0:4567'

# Same as dev, with ScreenScraper calls routed through the local caching proxy.
dev-proxy:
	bash -c 'source .env && export SCREENSCRAPER_API_BASE=http://127.0.0.1:8765/api2/ && mkdir -p data/logs && LOG_FILE=$$PWD/data/logs/server.log uv run python manage.py migrate && uv run python manage.py runserver 0.0.0.0:4567'

workers:
	bash -c 'source .env && trap "kill 0" EXIT SIGTERM SIGINT; uv run manage.py worker --queues=user_actions --concurrency=2 & uv run manage.py worker --queues=background,metadata --concurrency=8 & wait'

# Same as workers, with ScreenScraper calls routed through the local caching proxy.
workers-proxy:
	bash -c 'source .env && export SCREENSCRAPER_API_BASE=http://127.0.0.1:8765/api2/ && trap "kill 0" EXIT SIGTERM SIGINT; uv run manage.py worker --queues=user_actions --concurrency=2 & uv run manage.py worker --queues=background,metadata --concurrency=8 & wait'

watch:
	bash -c 'source .env && mkdir -p data/logs && LOG_FILE=$$PWD/data/logs/worker.log exec watchexec --restart --exts py --watch . --ignore .venv --ignore data --stop-signal SIGTERM -- make workers'

# Local read-through ScreenScraper cache (start this first).
proxy:
	uv run python scripts/screenscraper_proxy.py
