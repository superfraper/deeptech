.PHONY: help install format lint format-check lint-check clean

# Use the project's virtual environment Python by default
ifeq ($(OS),Windows_NT)
PYTHON := .venv/\Scripts/\python.exe
PRETTIER := node frontend/node_modules/prettier/bin/prettier.cjs
else
PYTHON := .venv/bin/python
PRETTIER := node frontend/node_modules/prettier/bin/prettier.cjs
endif

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies
	@echo "Installing Python dependencies..."
	$(PYTHON) -m pip install -r backend/requirements.txt
	@echo "Installing Node.js dependencies..."
	npm --prefix frontend install

format: ## Format all code (Python with Black, JS/TS with Prettier)
	@echo "Formatting Python code with Black..."
	$(PYTHON) -m black backend
	@echo "Formatting JavaScript/TypeScript code with Prettier..."
	npm --prefix frontend run format
	@echo "Formatting root files with Prettier..."
	$(PRETTIER) --no-error-on-unmatched-pattern --write "*.{md,json,yml,yaml}" ".github/**/*.{md,json,yml,yaml}"

format-check: ## Check code formatting without making changes
	@echo "Checking Python code formatting with Black..."
	$(PYTHON) -m black --check backend
	@echo "Checking JavaScript/TypeScript code formatting with Prettier..."
	npm --prefix frontend run format:check
	@echo "Checking root files formatting with Prettier..."
	$(PRETTIER) --no-error-on-unmatched-pattern --check "*.{md,json,yml,yaml}" ".github/**/*.{md,json,yml,yaml}"

lint: ## Lint all code (Python with Ruff, JS/TS with ESLint)
	@echo "Linting Python code with Ruff..."
	$(PYTHON) -m ruff check backend --fix
	@echo "Linting JavaScript/TypeScript code with ESLint..."
	npm --prefix frontend run lint:fix

lint-check: ## Check code linting without making changes
	@echo "Checking Python code with Ruff..."
	$(PYTHON) -m ruff check backend
	@echo "Checking JavaScript/TypeScript code with ESLint..."
	npm --prefix frontend run lint

check: format-check lint-check ## Run all checks without making changes

fix: format lint ## Format and lint all code (fixes issues)

clean: ## Clean up generated files
	@echo "Cleaning Python cache files..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name "*.pyo" -delete 2>/dev/null || true
	@echo "Cleaning Node.js files..."
	cd frontend && rm -rf node_modules package-lock.json 2>/dev/null || true
	@echo "Cleaning build artifacts..."
	rm -rf build/ dist/ .pytest_cache/ .ruff_cache/ 2>/dev/null || true

setup: install ## Install dependencies and set up the project
	@echo "Setting up pre-commit hooks..."
	@echo "Project setup complete!"

dev: ## Start development servers
	@echo "Starting backend server..."
	cd backend && $(PYTHON) -m uvicorn main:app --reload &
	@echo "Starting frontend server..."
	cd frontend && npm start &
	@echo "Development servers started. Press Ctrl+C to stop all servers."
