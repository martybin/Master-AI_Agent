.PHONY: setup run test test-ui check doctor n8n smoke
setup:
	uv sync --group dev
	uv run python scripts/bootstrap.py
run:
	uv run uvicorn master_agent.api:app --host 127.0.0.1 --port 8000 --workers 1
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
