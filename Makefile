.PHONY: setup run test test-ui check doctor n8n smoke smoke-local smoke-coding
setup:
	uv sync --group dev
	uv run python scripts/bootstrap.py
run:
	uv run biscode serve
test:
	uv run pytest -q
test-ui:
	node --test tests/ui.test.mjs
check:
	uv run ruff check .
	uv run ruff format --check .
doctor:
	uv run python scripts/doctor.py
n8n:
	docker compose up -d n8n
smoke:
	uv run python scripts/smoke_ollama.py
smoke-local:
	uv run python scripts/smoke_local.py
smoke-coding:
	uv run python scripts/smoke_coding.py
