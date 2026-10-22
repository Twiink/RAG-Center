UV := uv run --directory Backend rag-center
.PHONY: init dev serve migrate worker scheduler check test integration preflight doctor config \
        frontend-check frontend-dev openapi \
        compose-up compose-down compose-observability-up compose-observability-down \
        lint format-check typecheck
init:
	uv sync --project Backend --locked
	$(UV) init
dev:
	$(UV) dev
serve:
	$(UV) serve
migrate:
	$(UV) migrate
worker:
	$(UV) worker run
scheduler:
	$(UV) scheduler run
check:
	$(UV) check
lint:
	cd Backend && uv run ruff check src/
format-check:
	cd Backend && uv run ruff format --check src/
typecheck:
	cd Backend && uv run ty check
test:
	cd Backend && uv run pytest
integration:
	cd Backend && uv run pytest -m integration tests/integration
preflight:
	$(UV) preflight
doctor:
	$(UV) doctor
config:
	$(UV) config show
openapi:
	$(UV) openapi --output ../Frontend/openapi.json
	cd Frontend && npm run openapi
frontend-check:
	cd Frontend && npm run lint && npm run typecheck && npm run build
frontend-dev:
	cd Frontend && npm run dev
compose-up:
	docker compose -f Deploy/docker-compose.yml up -d --build
compose-down:
	docker compose -f Deploy/docker-compose.yml down
compose-observability-up:
	docker compose -f Deploy/docker-compose.yml -f Deploy/docker-compose.observability.yml up -d --build
compose-observability-down:
	docker compose -f Deploy/docker-compose.yml -f Deploy/docker-compose.observability.yml down
