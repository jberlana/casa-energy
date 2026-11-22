PYTHON ?= .venv/bin/python

.PHONY: help setup test build check check-publication demo dev-web
help:
	@echo 'setup | test | build | check | check-publication | demo | dev-web'
setup:
	python3 -m venv .venv
	"$(PYTHON)" -m pip install -r requirements-web.lock
	npm ci --prefix web
test:
	"$(PYTHON)" -m unittest discover -s tests -v
build:
	npm run build --prefix web
check: test build
	git diff --check
check-publication:
	"$(PYTHON)" scripts/check_publication.py
demo: build
	TADO_DATA_ROOT="$(CURDIR)/.local/history" TADO_ENV_ROOT="$(CURDIR)/.local/environment" TADO_WEB_ROOT="$(CURDIR)/.local/web" "$(PYTHON)" -m backend.web_server
dev-web:
	npm run dev --prefix web
