.PHONY: install test lint init-db daily weekly monthly dashboard

install:
	uv sync --extra dev

test:
	uv run python -m unittest discover -s tests -v

lint:
	uv run ruff check src tests

init-db:
	uv run paper-radar init-db

daily:
	uv run paper-radar daily --dry-run

weekly:
	uv run paper-radar weekly --dry-run

monthly:
	uv run paper-radar monthly --dry-run

dashboard:
	uv run paper-radar dashboard
