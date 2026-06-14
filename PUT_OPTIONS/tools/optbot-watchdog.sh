#!/bin/bash
# Watchdog for PUT_OPTIONS bots (OTM on 8082, ITM on 8083).
# Checks both services every 30s and restarts via systemd if health fails.

CHECK_INTERVAL=30
MAX_FAILURES=3

declare -A FAILURES=([pe-otm]=0 [pe-itm]=0)

declare -A PORTS=([pe-otm]=8082 [pe-itm]=8083)
declare -A SERVICES=([pe-otm]=pe-otm.service [pe-itm]=pe-itm.service)

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"; }

is_port_listening() {
    if command -v ss &>/dev/null; then ss -ltnH "( sport = :$1 )" 2>/dev/null | grep -q LISTEN; return $?; fi
    if command -v lsof &>/dev/null; then lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | tail -n +2 | grep -q .; return $?; fi
    netstat -tuln 2>/dev/null | grep -q ":$1 "
}

check_bot() {
    local name=$1
    local port=${PORTS[$name]}
    local svc=${SERVICES[$name]}
    local ok=true

    if ! systemctl is-active --quiet "$svc"; then
        log "[$name] Service not active"
        ok=false
    fi

    if ! is_port_listening "$port"; then
        log "[$name] Port $port not listening"
        ok=false
    fi

    local http=$(curl -s -m 5 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$port/health" 2>/dev/null || echo "000")
    if [ "$http" != "200" ]; then
        log "[$name] Health HTTP $http"
        ok=false
    fi

    if $ok; then
        FAILURES[$name]=0
        log "[$name] OK (port $port, health $http)"
        return 0
    fi

    FAILURES[$name]=$((FAILURES[$name] + 1))
    log "[$name] Failure ${FAILURES[$name]}/$MAX_FAILURES"

    if [ ${FAILURES[$name]} -ge $MAX_FAILURES ]; then
        log "[$name] Restarting $svc..."
        systemctl restart "$svc" && FAILURES[$name]=0 || log "[$name] Restart failed"
    fi
}

log "PE Options Watchdog started | OTM=8082 ITM=8083 | interval=${CHECK_INTERVAL}s"

trap 'log "Watchdog stopped"; exit 0' SIGTERM SIGINT

while true; do
    for bot in pe-otm pe-itm; do
        check_bot "$bot"
    done
    sleep "$CHECK_INTERVAL"
done
