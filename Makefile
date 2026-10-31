# Developer entry points for Winnie.
#
# `make api` sources apps/api/.env when it exists, as a local convenience. The
# application itself never reads that file: it takes secrets from the process
# environment, so exported variables work just as well.

SHELL := /usr/bin/env bash

ROOT := $(CURDIR)
API_DIR := $(ROOT)/apps/api
ANDROID_DIR := $(ROOT)/apps/android
ENV_FILE ?= $(API_DIR)/.env

API_HOST ?= ::
API_PORT ?= 8000
API_RELOAD ?= 1
API_RELOAD_FLAG := $(if $(filter 1 true yes on,$(API_RELOAD)),--reload,)
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
.PHONY: help api android stop restart

help: ## Show this help
	@printf 'Winnie developer commands\n\n'
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf '\nVariables:\n'
	@printf '  API_HOST=%s API_PORT=%s API_RELOAD=%s\n' '$(API_HOST)' '$(API_PORT)' '$(API_RELOAD)'
	@printf '  JAVA_HOME=%s\n' '$(JAVA_HOME)'
	@printf '  ANDROID_HOME=%s\n' '$(ANDROID_HOME)'
	@printf '\nLocal environment: %s (gitignored)\n' '$(ENV_FILE)'
	@printf '  WINNIE_LLM_API_KEY, VOLCENGINE_ASR_API_KEY  API secrets\n'
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

stop: ## Stop the API listening on API_PORT
	@set -euo pipefail; \
	port='$(API_PORT)'; \
	pids=$$(ss -ltnpH "sport = :$$port" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u || true); \
	if [ -z "$$pids" ]; then \
		printf 'Nothing is listening on port %s.\n' "$$port"; \
		exit 0; \
	fi; \
	targets=''; \
	for pid in $$pids; do \
		cmd=$$(ps -o args= -p "$$pid" 2>/dev/null || true); \
		ppid=$$(ps -o ppid= -p "$$pid" 2>/dev/null | tr -d ' ' || true); \
		parent=$$(ps -o args= -p "$$ppid" 2>/dev/null || true); \
		case "$$cmd" in \
			*uvicorn*winnie_api.main:app*) targets="$$targets $$pid";; \
			*) case "$$parent" in \
				*uvicorn*winnie_api.main:app*) targets="$$targets $$ppid";; \
				*) printf 'Port %s is held by pid %s, not the Winnie API; leaving it alone.\n' "$$port" "$$pid";; \
			esac;; \
		esac; \
	done; \
	if [ -z "$$targets" ]; then \
		printf 'Port %s is used by a process this target did not start; refusing to stop it.\n' "$$port" >&2; \
		exit 1; \
	fi; \
	targets=$$(printf '%s\n' $$targets | sort -u | paste -sd ' ' -); \
	printf 'Stopping the API on port %s (pid %s).\n' "$$port" "$$targets"; \
	kill $$targets 2>/dev/null || true; \
	for _ in $$(seq 1 50); do \
		if ! ss -ltnH "sport = :$$port" 2>/dev/null | grep -q .; then \
			printf 'Stopped.\n'; \
			exit 0; \
		fi; \
		sleep 0.1; \
	done; \
	printf 'Port %s is still open after 5s; sending SIGKILL.\n' "$$port" >&2; \
	kill -9 $$targets 2>/dev/null || true; \
	exit 1

restart: stop ## Stop the API, then start it again with the current environment
	@$(MAKE) --no-print-directory api

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
