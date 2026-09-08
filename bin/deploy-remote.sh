#!/usr/bin/env bash
#
# Runs ON THE API HOST, piped in over SSH by `make deploy`. The iris repo is
# not checked out on that host - everything here operates on the api-stack
# checkout at $COMPOSE_DIR.
#
# Expects in the environment (set by the ssh command line, so no AcceptEnv
# config is needed on the server):
#
#   IRIS_IMAGE   digest-pinned image ref, e.g. registry.../iris@sha256:...
#   COMPOSE_DIR  path to the api-stack checkout
#   SERVICE      compose service key
#   ENVIRONMENT  staging | production (informational)

set -euo pipefail

: "${IRIS_IMAGE:?not set - the ssh command line did not carry it through}"
: "${COMPOSE_DIR:?not set}"
: "${SERVICE:?not set}"

cd "$COMPOSE_DIR"

# Must be exported, not just set - compose reads it from the environment of
# the child process.
export IRIS_IMAGE

echo "==> $SERVICE -> $IRIS_IMAGE  [${ENVIRONMENT:-unknown}]"

# ---------------------------------------------------------------- assertion
# The compose file defaults to `iris:local` for local testing, so a variable
# that fails to plumb through resolves to a locally-built image instead of
# erroring. Verify compose sees exactly what we passed, before anything runs.
resolved=$(docker compose config --images "$SERVICE")
if [ "$resolved" != "$IRIS_IMAGE" ]; then
    echo "ERROR: compose resolved $SERVICE to '$resolved'"
    echo "       expected '$IRIS_IMAGE'"
    echo "       IRIS_IMAGE did not reach compose - check the variable name"
    echo "       in the compose file and in the Makefile deploy target."
    exit 1
fi

# ------------------------------------------------------------- pull and up
# Requires the SSH user's own `docker login` on this host, with credsStore
# removed from its ~/.docker/config.json.
docker compose pull "$SERVICE"

# The migration container uses the same IRIS_IMAGE and is a depends_on with
# service_completed_successfully, so `up -d` runs migrations first and will
# refuse to start the app if they fail. App and schema move together.
#
# A failed migration is the single most likely failure here, and set -e would
# otherwise abort with only compose's one-line error. Dump the whole project's
# recent logs so the migrator's output is actually visible in the build.
if ! docker compose up -d "$SERVICE"; then
    echo "ERROR: compose up failed - dependency or migration likely failed"
    docker compose logs --tail=100 || true
    exit 1
fi

# ----------------------------------------------------------- health gating
# No pipe to `head`: under `set -o pipefail`, head closing the pipe early can
# SIGPIPE compose and fail the whole pipeline. Trim in the shell instead.
# -a is required: without it, compose v2 lists only RUNNING containers, so a
# service that crashed on startup returns empty and gets misreported as
# "never created".
cid=$(docker compose ps -aq "$SERVICE")
cid=${cid%%$'\n'*}
if [ -z "$cid" ]; then
    echo "ERROR: no container was created for $SERVICE"
    docker compose logs --tail=50 "$SERVICE" || true
    exit 1
fi

echo "==> waiting for $SERVICE to settle"

# Without a healthcheck, "running" is true the instant up -d returns, so a
# container that crashes three seconds later would pass. Require it to stay
# running across several consecutive polls instead of exiting on the first.
stable=0
stable_required=5          # 5 x 2s = 10s continuously up

deadline=$(( $(date +%s) + 90 ))

while [ "$(date +%s)" -lt "$deadline" ]; do
    state=$(docker inspect -f '{{.State.Status}}' "$cid")
    # The {{if}} guard is required - the template errors on a nil Health
    # field when the service has no healthcheck defined.
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid")
    restarts=$(docker inspect -f '{{.RestartCount}}' "$cid")

    # A restart policy can bounce a crashing container back to "running"
    # between polls, so state alone can look fine while it crash-loops.
    if [ "$restarts" -gt 0 ]; then
        echo "ERROR: $SERVICE has restarted $restarts time(s) - crash looping"
        docker compose logs --tail=50 "$SERVICE"
        exit 1
    fi

    case "$state:$health" in
        running:healthy)
            echo "==> $SERVICE healthy"
            exit 0
            ;;
        running:none)
            stable=$((stable + 1))
            if [ "$stable" -ge "$stable_required" ]; then
                echo "==> $SERVICE up for 10s (no healthcheck defined - weak check)"
                echo "    add a healthcheck: to the $SERVICE service to make this meaningful"
                exit 0
            fi
            ;;
        exited:*|dead:*)
            echo "ERROR: $SERVICE exited during startup"
            docker compose logs --tail=50 "$SERVICE"
            exit 1
            ;;
        *)
            stable=0
            ;;
    esac
    sleep 2
done

echo "ERROR: $SERVICE did not become healthy within 90s"
docker compose logs --tail=50 "$SERVICE"
exit 1
