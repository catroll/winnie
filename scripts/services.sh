#!/usr/bin/env bash
# Start, stop, restart and inspect the Winnie local services.
#
# The individual Make targets (`api`, `admin`, `index-serve`) stay in the
# foreground. This script detaches them with logs and PID files, so one command
# can manage the full local stack. Ports are the contract for stop: a process is
# only killed when its command line belongs to the expected service.

set -euo pipefail

ROOT="${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICES="${SERVICES:-api index admin}"
API_HOST="${API_HOST:-::}"
API_PORT="${API_PORT:-8000}"
API_RELOAD="${API_RELOAD:-1}"
ENV_FILE="${ENV_FILE:-$ROOT/apps/api/.env}"
ADMIN_HOST="${ADMIN_HOST:-127.0.0.1}"
ADMIN_PORT="${ADMIN_PORT:-5173}"
ADMIN_API_TARGET="${ADMIN_API_TARGET:-http://127.0.0.1:$API_PORT}"
INDEX_HOST="${INDEX_HOST:-127.0.0.1}"
INDEX_PORT="${INDEX_PORT:-8790}"
INDEX_CONFIG="${INDEX_CONFIG:-$HOME/.config/winnie-index/config.yaml}"
INDEX_PYTHON="${INDEX_PYTHON:-$ROOT/apps/api/.venv/bin/python}"

RUN_DIR="$ROOT/.cache/run"
LOG_DIR="$ROOT/.cache/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

export API_HOST API_PORT API_RELOAD ENV_FILE
export ADMIN_HOST ADMIN_PORT ADMIN_API_TARGET
export INDEX_HOST INDEX_PORT INDEX_CONFIG INDEX_PYTHON

die() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

service_port() {
    case "$1" in
        api) printf '%s' "$API_PORT" ;;
        admin) printf '%s' "$ADMIN_PORT" ;;
        index) printf '%s' "$INDEX_PORT" ;;
        *) return 1 ;;
    esac
}

service_target() {
    case "$1" in
        api) printf 'api' ;;
        admin) printf 'admin' ;;
        index) printf 'index-serve' ;;
        *) return 1 ;;
    esac
}

service_label() {
    case "$1" in
        api) printf 'API' ;;
        admin) printf 'Admin' ;;
        index) printf 'Index' ;;
        *) printf '%s' "$1" ;;
    esac
}

port_is_open() {
    ss -ltnH "sport = :$1" 2>/dev/null | grep -q .
}

listener_pids() {
    ss -ltnpH "sport = :$1" 2>/dev/null \
        | grep -o 'pid=[0-9]*' \
        | cut -d= -f2 \
        | sort -u \
        || true
}

process_matches() {
    local name="$1" pid="$2" cmd cwd
    cmd="$(ps -o args= -p "$pid" 2>/dev/null || true)"
    [ -n "$cmd" ] || return 1
    case "$name" in
        api)
            [[ "$cmd" == *uvicorn*winnie_api.main:app* ]]
            ;;
        admin)
            [[ "$cmd" == *vite* ]] || return 1
            cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
            [[ "$cwd" == "$ROOT/apps/admin"* || "$cmd" == *"$ROOT/apps/admin"* ]]
            ;;
        index)
            [[ "$cmd" == *winnie_index* ]]
            ;;
        *)
            return 1
            ;;
    esac
}

owning_pid() {
    local name="$1" pid="$2" parent
    if process_matches "$name" "$pid"; then
        printf '%s\n' "$pid"
        return 0
    fi
    parent="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    if [ -n "$parent" ] && process_matches "$name" "$parent"; then
        printf '%s\n' "$parent"
        return 0
    fi
    return 1
}

owning_pids() {
    local name="$1" port="$2" pid
    for pid in $(listener_pids "$port"); do
        owning_pid "$name" "$pid" || true
    done | sort -u
}

preflight() {
    local name="$1"
    case "$name" in
        api)
            if ! command -v uv >/dev/null 2>&1 \
                && [ ! -x "$HOME/.local/bin/uv" ] \
                && [ ! -x "$ROOT/apps/api/.venv/bin/uvicorn" ]; then
                die "the API needs uv or apps/api/.venv; run: cd apps/api && uv sync"
            fi
            ;;
        admin)
            [ -d "$ROOT/apps/admin/node_modules" ] \
                || die "admin dependencies are missing; run: cd apps/admin && npm install"
            command -v npm >/dev/null 2>&1 \
                || die "npm is not on PATH"
            ;;
        index)
            [ -f "$INDEX_CONFIG" ] \
                || die "index config is missing: $INDEX_CONFIG (run: make index-init)"
            [ -x "$INDEX_PYTHON" ] \
                || die "index interpreter is missing: $INDEX_PYTHON"
            ;;
        *)
            die "unknown service: $name (expected api, admin or index)"
            ;;
    esac
}

health_check() {
    local name="$1" port="$2"
    case "$name" in
        api)
            curl -fsS -m 2 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1 \
                || curl -fsS -m 2 "http://[::1]:$port/healthz" >/dev/null 2>&1
            ;;
        admin)
            curl -fsS -m 2 "http://$ADMIN_HOST:$port/" >/dev/null 2>&1
            ;;
        index)
            curl -fsS -m 2 "http://$INDEX_HOST:$port/healthz" >/dev/null 2>&1
            ;;
        *)
            return 1
            ;;
    esac
}

wait_ready() {
    local name="$1" port="$2" log="$3" pidfile="$4" attempt
    for attempt in $(seq 1 300); do
        if health_check "$name" "$port"; then
            return 0
        fi
        if [ -f "$pidfile" ]; then
            local pid
            pid="$(cat "$pidfile" 2>/dev/null || true)"
            if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
        fi
        sleep 0.2
    done
    printf '\n%s failed to become healthy.\n' "$(service_label "$name")" >&2
    tail -n 30 "$log" >&2 || true
    return 1
}

start_one() {
    local name="$1" port target log pidfile owners
    port="$(service_port "$name")"
    target="$(service_target "$name")"
    log="$LOG_DIR/$name.log"
    pidfile="$RUN_DIR/$name.pid"

    if port_is_open "$port"; then
        owners="$(owning_pids "$name" "$port" || true)"
        if [ -n "$owners" ]; then
            printf '%s is already running on port %s (pid %s).\n' \
                "$(service_label "$name")" "$port" "$(printf '%s' "$owners" | paste -sd ' ' -)"
            return 0
        fi
        printf 'Port %s is held by a process that is not the Winnie %s.\n' \
            "$port" "$name" >&2
        return 1
    fi

    preflight "$name"
    : >"$log"
    printf 'Starting %s on port %s... ' "$(service_label "$name")" "$port"
    (
        cd "$ROOT"
        nohup make --no-print-directory "$target" >>"$log" 2>&1 &
        echo $! >"$pidfile"
    )
    if wait_ready "$name" "$port" "$log" "$pidfile"; then
        printf 'ready\n'
        return 0
    fi
    return 1
}

cleanup_pidfile() {
    local name="$1" pidfile="$RUN_DIR/$1.pid" pid
    [ -f "$pidfile" ] || return 0
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
}

stop_one() {
    local name="$1" port targets attempt
    port="$(service_port "$name")"
    if ! port_is_open "$port"; then
        cleanup_pidfile "$name"
        printf '%s is not running.\n' "$(service_label "$name")"
        return 0
    fi

    targets="$(owning_pids "$name" "$port" || true)"
    if [ -z "$targets" ]; then
        printf 'Refusing to stop port %s: it is not the Winnie %s.\n' \
            "$port" "$name" >&2
        return 1
    fi

    printf 'Stopping %s on port %s (pid %s).\n' \
        "$(service_label "$name")" "$port" "$(printf '%s' "$targets" | paste -sd ' ' -)"
    kill $targets 2>/dev/null || true
    for attempt in $(seq 1 50); do
        if ! port_is_open "$port"; then
            cleanup_pidfile "$name"
            printf 'Stopped.\n'
            return 0
        fi
        sleep 0.1
    done
    printf 'Port %s is still open after 5s; sending SIGKILL.\n' "$port" >&2
    kill -9 $targets 2>/dev/null || true
    cleanup_pidfile "$name"
    return 1
}

start_all() {
    local name
    for name in $SERVICES; do
        preflight "$name"
    done
    for name in $SERVICES; do
        start_one "$name" || {
            printf 'Start failed; run make stop to clean up partial services.\n' >&2
            return 1
        }
    done
    printf '\n'
    for name in $SERVICES; do
        case "$name" in
            api) printf 'API     http://[::]:%s\n' "$API_PORT" ;;
            index) printf 'Index   http://%s:%s\n' "$INDEX_HOST" "$INDEX_PORT" ;;
            admin) printf 'Admin   http://%s:%s\n' "$ADMIN_HOST" "$ADMIN_PORT" ;;
        esac
    done
    printf 'Logs    %s\n' "$LOG_DIR"
}

stop_all() {
    local names=($SERVICES) index rc=0
    for ((index = ${#names[@]} - 1; index >= 0; index--)); do
        stop_one "${names[index]}" || rc=1
    done
    return "$rc"
}

status_all() {
    local name port owners state pid
    printf '%-8s %-7s %-12s %s\n' 'SERVICE' 'PORT' 'STATE' 'PID'
    for name in $SERVICES; do
        port="$(service_port "$name")"
        if ! port_is_open "$port"; then
            printf '%-8s %-7s %-12s %s\n' "$(service_label "$name")" "$port" 'stopped' '-'
            continue
        fi
        owners="$(owning_pids "$name" "$port" || true)"
        if [ -z "$owners" ]; then
            printf '%-8s %-7s %-12s %s\n' \
                "$(service_label "$name")" "$port" 'foreign' "$(listener_pids "$port" | paste -sd ',' -)"
            continue
        fi
        pid="$(printf '%s' "$owners" | paste -sd ',' -)"
        if health_check "$name" "$port"; then
            state='healthy'
        else
            state='unhealthy'
        fi
        printf '%-8s %-7s %-12s %s\n' "$(service_label "$name")" "$port" "$state" "$pid"
    done
    printf '\nLogs: %s\n' "$LOG_DIR"
}

case "${1:-}" in
    start)
        start_all
        ;;
    stop)
        stop_all
        ;;
    restart)
        stop_all || true
        start_all
        ;;
    status)
        status_all
        ;;
    *)
        die "usage: $0 {start|stop|restart|status}"
        ;;
esac
