UV := uv run --directory Backend rag-center
.PHONY: init dev serve migrate worker scheduler check test integration preflight doctor config frontend-check frontend-dev openapi compose-up compose-down
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
	docker compose up -d --build
compose-down:
	docker compose down
