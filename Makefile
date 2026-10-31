# Developer entry points for Winnie.
#
# `make api` sources apps/api/.env when it exists, as a local convenience. The
# application itself never reads that file: it takes secrets from the process
# environment, so exported variables work just as well.

SHELL := /usr/bin/env bash

ROOT := $(CURDIR)
API_DIR := $(ROOT)/apps/api
ANDROID_DIR := $(ROOT)/apps/android
INDEX_DIR := $(ROOT)/packages/winnie-index
ADMIN_DIR := $(ROOT)/apps/admin
NEWS_DIR := $(ROOT)/packages/winnie-news
ENV_FILE ?= $(API_DIR)/.env
INDEX_PYTHON ?= $(API_DIR)/.venv/bin/python
INDEX_CONFIG ?= $(HOME)/.config/winnie-index/config.yaml

API_HOST ?= ::
API_PORT ?= 8000
API_RELOAD ?= 1
API_RELOAD_FLAG := $(if $(filter 1 true yes on,$(API_RELOAD)),--reload,)
# Managed local services, in start order. Stop runs in reverse order.
SERVICES ?= api index admin
ADMIN_HOST ?= 127.0.0.1
ADMIN_PORT ?= 5173
INDEX_HOST ?= 127.0.0.1
INDEX_PORT ?= 8790
# The admin dev server proxies its API prefixes to this address.
ADMIN_API_TARGET ?= http://127.0.0.1:$(API_PORT)
# $HOME/.cache is read-only here, so keep the uv cache in the checkout.
UV_CACHE_DIR ?= $(ROOT)/.cache/uv

# Keep every toolchain root inside the checkout. The default Gradle location
# under $HOME is not writable in this environment; see docs/LESSONS-android.md.
BUILD_HOME ?= $(ROOT)/.home
GRADLE_USER_HOME ?= $(ROOT)/.gradle-home
ANDROID_HOME ?= $(ROOT)/.android-sdk
ANDROID_USER_HOME ?= $(ROOT)/.android-user
JAVA_HOME ?= $(shell readlink -f "$$(command -v java)" 2>/dev/null | sed 's:/bin/java$$::')

.DEFAULT_GOAL := help
.PHONY: help api android admin admin-build start stop restart status news-fetch index-init index-dry index-build index-serve index-status index-search index-skill index-test

help: ## Show this help
	@printf 'Winnie developer commands\n\n'
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf '\nVariables:\n'
	@printf '  API_HOST=%s API_PORT=%s API_RELOAD=%s\n' '$(API_HOST)' '$(API_PORT)' '$(API_RELOAD)'
	@printf '  SERVICES=%s\n' '$(SERVICES)'
	@printf '  ADMIN_HOST=%s ADMIN_PORT=%s\n' '$(ADMIN_HOST)' '$(ADMIN_PORT)'
	@printf '  INDEX_HOST=%s INDEX_PORT=%s\n' '$(INDEX_HOST)' '$(INDEX_PORT)'
	@printf '  JAVA_HOME=%s\n' '$(JAVA_HOME)'
	@printf '  ANDROID_HOME=%s\n' '$(ANDROID_HOME)'
	@printf '  ADMIN_API_TARGET=%s\n' '$(ADMIN_API_TARGET)'
	@printf '\nLocal environment: %s (gitignored)\n' '$(ENV_FILE)'
	@printf '  DEEPSEEK_API_KEY, VOLCENGINE_ASR_API_KEY  API secrets\n'
	@printf '  WINNIE_PUBLIC_URL                           Android default server URL\n'
	@printf 'Without WINNIE_PUBLIC_URL the APK falls back to http://10.0.2.2:8000.\n'

api: ## Run the API on [::]:8000, reachable over IPv6, with reload enabled
	@cd '$(API_DIR)' && \
	if [ -f '$(ENV_FILE)' ]; then \
		set -a; . '$(ENV_FILE)'; set +a; \
		printf 'Loaded local environment from %s\n' '$(ENV_FILE)'; \
	fi; \
	if command -v uv >/dev/null 2>&1; then \
		exec env UV_CACHE_DIR='$(UV_CACHE_DIR)' uv run uvicorn winnie_api.main:app --host '$(API_HOST)' --port '$(API_PORT)' $(API_RELOAD_FLAG); \
	elif [ -x "$$HOME/.local/bin/uv" ]; then \
		exec env UV_CACHE_DIR='$(UV_CACHE_DIR)' "$$HOME/.local/bin/uv" run uvicorn winnie_api.main:app --host '$(API_HOST)' --port '$(API_PORT)' $(API_RELOAD_FLAG); \
	elif [ -x .venv/bin/uvicorn ]; then \
		exec .venv/bin/uvicorn winnie_api.main:app --host '$(API_HOST)' --port '$(API_PORT)' $(API_RELOAD_FLAG); \
	else \
		printf 'error: uv is not installed and apps/api/.venv is missing\n' >&2; \
		printf '       run: cd apps/api && uv sync\n' >&2; \
		exit 1; \
	fi

# All-service lifecycle. The individual targets above stay in the foreground
# for development; these start detached processes with logs under .cache/.
SERVICE_ENV = ROOT='$(ROOT)' API_HOST='$(API_HOST)' API_PORT='$(API_PORT)' API_RELOAD='$(API_RELOAD)' ENV_FILE='$(ENV_FILE)' ADMIN_HOST='$(ADMIN_HOST)' ADMIN_PORT='$(ADMIN_PORT)' ADMIN_API_TARGET='$(ADMIN_API_TARGET)' INDEX_HOST='$(INDEX_HOST)' INDEX_PORT='$(INDEX_PORT)' INDEX_CONFIG='$(INDEX_CONFIG)' INDEX_PYTHON='$(INDEX_PYTHON)'

start: ## Start all SERVICES in the background (API, index, admin)
	@$(SERVICE_ENV) SERVICES='$(SERVICES)' bash '$(ROOT)/scripts/services.sh' start

stop: ## Stop all SERVICES (reverse start order)
	@$(SERVICE_ENV) SERVICES='$(SERVICES)' bash '$(ROOT)/scripts/services.sh' stop

restart: ## Stop then start all SERVICES
	@$(SERVICE_ENV) SERVICES='$(SERVICES)' bash '$(ROOT)/scripts/services.sh' restart

status: ## Show each managed service, its port and health
	@$(SERVICE_ENV) SERVICES='$(SERVICES)' bash '$(ROOT)/scripts/services.sh' status

admin: ## Run the admin console in the foreground (ADMIN_HOST:ADMIN_PORT)
	@test -d '$(ADMIN_DIR)/node_modules' || { \
		printf 'error: admin dependencies are not installed\n' >&2; \
		printf '       run: cd apps/admin && npm install\n' >&2; \
		exit 1; \
	}
	@printf 'Admin console: http://%s:%s (API proxy %s)\n' '$(ADMIN_HOST)' '$(ADMIN_PORT)' '$(ADMIN_API_TARGET)'
	@cd '$(ADMIN_DIR)' && VITE_API_PROXY_TARGET='$(ADMIN_API_TARGET)' npm run dev -- --host '$(ADMIN_HOST)' --port '$(ADMIN_PORT)'

admin-build: ## Build the admin console into apps/admin/dist
	@test -d '$(ADMIN_DIR)/node_modules' || { \
		printf 'error: admin dependencies are not installed\n' >&2; \
		printf '       run: cd apps/admin && npm install\n' >&2; \
		exit 1; \
	}
	@cd '$(ADMIN_DIR)' && npm run build

android: ## Build the debug APK at apps/android/app/build/outputs/apk/debug/
	@test -d '$(JAVA_HOME)' || { printf 'error: JAVA_HOME not found: %s\n' '$(JAVA_HOME)' >&2; exit 1; }
	@test -d '$(ANDROID_HOME)' || { printf 'error: Android SDK not found: %s\n' '$(ANDROID_HOME)' >&2; printf '       set ANDROID_HOME or install the SDK\n' >&2; exit 1; }
	@mkdir -p '$(BUILD_HOME)' '$(GRADLE_USER_HOME)' '$(ANDROID_USER_HOME)'
	@if [ ! -f '$(ANDROID_DIR)/local.properties' ]; then \
		printf 'sdk.dir=%s\n' '$(ANDROID_HOME)' > '$(ANDROID_DIR)/local.properties'; \
	fi
	@cd '$(ANDROID_DIR)' && \
	if [ -f '$(ENV_FILE)' ]; then set -a; . '$(ENV_FILE)'; set +a; fi; \
	printf 'Default server URL: %s\n' "$${WINNIE_PUBLIC_URL:-http://10.0.2.2:8000}"; \
	HOME='$(BUILD_HOME)' \
	GRADLE_USER_HOME='$(GRADLE_USER_HOME)' \
	ANDROID_HOME='$(ANDROID_HOME)' \
	ANDROID_SDK_ROOT='$(ANDROID_HOME)' \
	ANDROID_USER_HOME='$(ANDROID_USER_HOME)' \
	JAVA_HOME='$(JAVA_HOME)' \
	./gradlew :app:assembleDebug --console=plain
	@printf '\nAPK: %s\n' '$(ANDROID_DIR)/app/build/outputs/apk/debug/app-debug.apk'

# News material collectors. Writes one material document per source; no model
# call and no API server are involved, so this is the quickest way to see
# whether a feed still works.
NEWS_MATERIAL_OUT ?= /tmp/winnie-news-material.json
news-fetch: ## Collect news material to /tmp/winnie-news-material.json
	@PYTHONPATH='$(NEWS_DIR)/src' '$(INDEX_PYTHON)' -m winnie_news.material.fetcher \
		-o '$(NEWS_MATERIAL_OUT)' --compact
	@printf 'News material: %s\n' '$(NEWS_MATERIAL_OUT)'

# Local document index. Runs with the API venv so nothing extra is installed;
# `skill install` writes ~/.agents/skills/local-knowledge/SKILL.md.
INDEX_RUN := PYTHONPATH='$(INDEX_DIR)/src' '$(INDEX_PYTHON)' -m winnie_index.cli --config '$(INDEX_CONFIG)'

index-init: ## Write an annotated ~/.config/winnie-index/config.yaml
	@$(INDEX_RUN) init

index-dry: ## Report what the index would ingest, writing nothing
	@$(INDEX_RUN) build --dry-run

index-build: ## Sweep the configured roots and index what changed
	@$(INDEX_RUN) build

index-serve: ## Run the index daemon (sweep loop, HTTP and MCP) in the foreground
	@$(INDEX_RUN) serve --host '$(INDEX_HOST)' --port '$(INDEX_PORT)'

index-status: ## Show index coverage and failures
	@$(INDEX_RUN) status

index-search: ## Search the index: make index-search Q="deployment notes"
	@$(INDEX_RUN) search '$(Q)'

index-skill: ## Install the Agent Skill into ~/.agents/skills/local-knowledge
	@$(INDEX_RUN) skill install

index-test: ## Run the index test suite
	@PYTHONPATH='$(INDEX_DIR)/src' '$(INDEX_PYTHON)' -m pytest '$(INDEX_DIR)/tests' -q
