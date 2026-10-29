#!/usr/bin/env bash
# Build-independent smoke test for the Docker image: starts a container, checks
# that the API is token-gated, the page is served, the health probe passes and
# the CLI inside the image knows its commands. Usage: scripts/docker-smoke.sh [image]
set -euo pipefail
IMAGE="${1:-gdrive-dedupe:ci}"
NAME="gdrive-dedupe-smoke-$$"
PORT="${SMOKE_PORT:-18765}"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$NAME" -p "127.0.0.1:${PORT}:8765" "$IMAGE" >/dev/null
for _ in $(seq 1 30); do
  curl -sf -o /dev/null "http://127.0.0.1:${PORT}/" && break
  sleep 1
done
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

TOKEN=$(docker logs "$NAME" 2>&1 | grep -o 'token=[A-Za-z0-9_-]*' | head -1 | cut -d= -f2)
[ -n "$TOKEN" ] || { echo "no access token printed at startup"; docker logs "$NAME"; exit 1; }
[ "$(code "http://127.0.0.1:${PORT}/")" = 200 ] || { echo "index page not served"; exit 1; }
[ "$(code "http://127.0.0.1:${PORT}/api/auth/status")" = 401 ] || { echo "API reachable without token"; exit 1; }
[ "$(code -H "X-Dedupe-Token: $TOKEN" "http://127.0.0.1:${PORT}/api/auth/status")" = 200 ] || { echo "token not accepted"; exit 1; }
[ "$(code -H "Host: evil.example.com" -H "X-Dedupe-Token: $TOKEN" "http://127.0.0.1:${PORT}/api/auth/status")" = 400 ] || { echo "foreign Host accepted"; exit 1; }
docker exec "$NAME" python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/').status==200 else 1)"
[ "$(docker exec "$NAME" id -u)" = 1000 ] || { echo "container runs as root"; exit 1; }
# Capture first, then grep: `grep -q` closing the pipe early would make `docker run`
# fail with "broken pipe" under pipefail.
HELP=$(docker run --rm "$IMAGE" --help)
for cmd in purge largest-files clean; do
  grep -q "$cmd" <<<"$HELP" || { echo "CLI inside the image lacks '$cmd'"; exit 1; }
done
echo "docker smoke test passed ($IMAGE)"
