.PHONY: dev workers watch

dev:
	bash -c 'source .env && mkdir -p data/logs && LOG_FILE=$$PWD/data/logs/server.log uv run python manage.py migrate && uv run python manage.py runserver 0.0.0.0:4567'

workers:
	bash -c 'source .env && trap "kill 0" EXIT SIGTERM SIGINT; uv run manage.py worker --queues=user_actions --concurrency=2 & uv run manage.py worker --queues=background,metadata --concurrency=8 & wait'

watch:
	bash -c 'source .env && mkdir -p data/logs && LOG_FILE=$$PWD/data/logs/worker.log exec watchexec --restart --exts py --watch . --ignore .venv --ignore data --stop-signal SIGTERM -- make workers'
