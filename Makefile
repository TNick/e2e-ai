.PHONY: help init init-d lint delint test pre-commit pre-commit-install \
	pre-commit-run format check monitor-ui-install monitor-ui-build

PYTHON ?= python
PIP ?= $(PYTHON) -m pip
PYTEST ?= $(PYTHON) -m pytest
RUFF ?= $(PYTHON) -m ruff
PRE_COMMIT ?= $(PYTHON) -m pre_commit
NPM ?= npm
MONITOR_UI_DIR ?= monitor-ui

help:
	@echo "e2e-ai development commands"
	@echo ""
	@echo "  init               Install the project in editable mode"
	@echo "  init-d             Install the project in editable mode with dev deps"
	@echo "  lint               Run Ruff checks"
	@echo "  delint             Run Ruff format and fixable checks"
	@echo "  format             Run Ruff format only"
	@echo "  test               Run pytest"
	@echo "  pre-commit         Run pre-commit on all files"
	@echo "  pre-commit-install Install pre-commit and register the hook"
	@echo "  check              Run lint and test"

init:
	$(PIP) install -e .

init-d:
	$(PIP) install -e ".[dev]"

lint:
	$(RUFF) check src tests

delint:
	$(RUFF) format src tests
	$(RUFF) check --fix src tests

format:
	$(RUFF) format src tests

test:
	$(PYTEST) -q

pre-commit-install:
	$(PIP) install pre-commit
	$(PRE_COMMIT) install

pre-commit:
	$(PRE_COMMIT) run --show-diff-on-failure --all-files

check: lint test

# Monitor UI (Node is only needed to *build* the UI, never to run e2e-ai).
monitor-ui-install:
	cd $(MONITOR_UI_DIR) && $(NPM) install

# Builds the Vite/React/MUI source and copies the bundle into the wheel's
# static assets at src/e2e_ai/monitor/static/.
monitor-ui-build:
	cd $(MONITOR_UI_DIR) && $(NPM) run build
