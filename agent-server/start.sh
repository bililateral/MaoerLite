#!/usr/bin/env bash
# User-directory deployment; reuse an existing Conda Python without changing it.
set -euo pipefail
umask 077
APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME="$APP_ROOT/.local"
AGENT_PYTHON="${AGENT_PYTHON:-$HOME/miniconda3/envs/py310/bin/python}"
mkdir -p "$RUNTIME"
export PYTHONPATH="$RUNTIME/packages"
export PYTHONDONTWRITEBYTECODE=1

owned_pid() {
    [[ -f "$RUNTIME/agent-server.pid" ]] || return 1
    local pid
    pid="$(cat "$RUNTIME/agent-server.pid")"
    [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/cmdline" ]] || return 1
    tr '\0' '\n' < "/proc/$pid/cmdline" | grep -Fxq -- "$APP_ROOT/agent-server/main.py" || return 1
    [[ "$(readlink -f "/proc/$pid/exe")" == "$(readlink -f "$AGENT_PYTHON")" ]] || return 1
    printf '%s\n' "$pid"
}

case "${1:-start}" in
    run)
        cd "$APP_ROOT"
        exec 9>"$RUNTIME/agent-server.lock"
        flock -n 9 || { printf 'Agent server lock is already held.\n' >&2; exit 1; }
        printf '%s\n' "$$" > "$RUNTIME/agent-server.pid"
        exec "$AGENT_PYTHON" -B "$APP_ROOT/agent-server/main.py"
        ;;
    start)
        [[ -x "$AGENT_PYTHON" ]] || { printf 'Set AGENT_PYTHON to an existing Conda Python.\n' >&2; exit 1; }
        if pid="$(owned_pid)"; then printf 'Agent server running; PID=%s\n' "$pid"; exit 0; fi
        nohup bash "$APP_ROOT/agent-server/start.sh" run >>"$RUNTIME/agent-server.log" 2>&1 < /dev/null &
        printf 'Agent server launched; check the health endpoint and .local/agent-server.log.\n'
        ;;
    stop)
        if pid="$(owned_pid)"; then
            kill -TERM "$pid"
            for attempt in {1..50}; do
                if [[ "$(owned_pid || true)" != "$pid" ]]; then
                    if [[ -f "$RUNTIME/agent-server.pid" && "$(cat "$RUNTIME/agent-server.pid")" == "$pid" ]]; then
                        rm -- "$RUNTIME/agent-server.pid"
                    fi
                    printf 'Agent server stopped; PID=%s\n' "$pid"
                    exit 0
                fi
                sleep 0.2
            done
            printf 'Server is still shutting down; check status before starting again.\n' >&2
            exit 1
        else printf 'No verified Agent process is running.\n'; fi
        ;;
    status)
        if pid="$(owned_pid)"; then printf 'Agent server running; PID=%s\n' "$pid"
        else printf 'Agent server is not running.\n'; exit 1; fi
        ;;
    *) printf 'Usage: bash agent-server/start.sh {start|stop|status}\n' >&2; exit 2 ;;
esac
