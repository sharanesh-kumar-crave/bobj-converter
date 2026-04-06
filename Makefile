.PHONY: install test lint format typecheck clean run deploy-dev deploy-prod

# ── Setup ──────────────────────────────────────────────────────────────────────
install:
	cd backend && pip install -r requirements.txt -r requirements-dev.txt

# ── Tests ──────────────────────────────────────────────────────────────────────
test:
	cd backend && pytest tests/ -v --cov=app --cov-report=term-missing

test-unit:
	cd backend && pytest tests/unit/ -v

test-integration:
	cd backend && pytest tests/integration/ -v

test-ci:
	cd backend && pytest tests/ --cov=app --cov-report=xml --junitxml=reports/results.xml

# ── Lint & format ──────────────────────────────────────────────────────────────
lint:
	cd backend && ruff check app/ tests/

format:
	cd backend && ruff format app/ tests/

format-check:
	cd backend && ruff format --check app/ tests/

typecheck:
	cd backend && mypy app/ --ignore-missing-imports

security:
	cd backend && bandit -r app/ -ll

check: lint format-check typecheck security test
	@echo "All checks passed ✓"

# ── Dev server ─────────────────────────────────────────────────────────────────
run:
	cd backend && uvicorn app.main:app --reload --port 8000

# ── CF deploys (local, uses logged-in CF CLI) ──────────────────────────────────
deploy-dev:
	@echo "Deploying to Dev CF space..."
	cf target -s dev
	cf push bobj-converter-api-dev -f backend/manifest.yml --strategy rolling
	cf push bobj-converter-ui-dev  -f frontend/manifest.yml --strategy rolling

deploy-prod:
	@echo "Deploying to Prod CF space..."
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ]
	cf target -s prod
	cf push bobj-converter-api -f backend/manifest.yml --strategy rolling
	cf push bobj-converter-ui  -f frontend/manifest.yml --strategy rolling

# ── DB schema ─────────────────────────────────────────────────────────────────
schema-apply:
	@echo "Applying HANA Cloud schema..."
	@echo "Run: db/schema.sql via HANA Cockpit or DBeaver"

# ── Clean ──────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf backend/reports/ backend/.coverage backend/coverage.xml
