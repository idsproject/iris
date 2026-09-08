#!/usr/bin/env bash
#
# Runs ON THE API HOST, piped in over SSH by `make deploy`. The service repo is
# not checked out on that host - everything here operates on the compose
# checkout at $COMPOSE_DIR.
#
# Expects in the environment (set on the ssh command line, so no AcceptEnv
# config is needed on the server):
#
#   IMAGE_VAR    compose variable name for this service, e.g. IRIS_IMAGE
#   IMAGE_REF    digest-pinned ref, e.g. registry.../iris@sha256:...
#   COMPOSE_DIR  path to the compose checkout
#   SERVICE      compose service key
#   ENVIRONMENT  staging | production (informational)
#
# ---------------------------------------------------------------------------
# WHY THE ENV FILES
#
# Every image in the compose file is ${SERVICE_IMAGE:?} - required, no
# default. Compose interpolates the WHOLE file on every command, even when a
# single service is targeted, so deploying iris still has to supply a value for
# logic, and vice versa. Passing only this service's variable inline fails with:
#
#   required variable LOGIC_IMAGE is missing a value
#
# So each pipeline owns one file under $COMPOSE_DIR/images/, writes only its
# own, and every compose call loads all of them. No shared mutable file, so two
# pipelines deploying at once cannot clobber each other or roll one another
# back to a stale value.
#
# The list is assembled by globbing images/, so a service added later is picked
# up with no change here. Note that .bashrc is NOT read by this script - ssh
# runs it non-interactively - which is why it sets COMPOSE_ENV_FILES itself
# rather than relying on the host's shell profile.

set -euo pipefail

: "${IMAGE_VAR:?not set - the ssh command line did not carry it through}"
: "${IMAGE_REF:?not set - the ssh command line did not carry it through}"
: "${COMPOSE_DIR:?not set}"
: "${SERVICE:?not set}"

cd "$COMPOSE_DIR"

[ -f .env ] || { echo "ERROR: no .env in $COMPOSE_DIR"; exit 1; }

echo "==> $SERVICE -> $IMAGE_REF  [${ENVIRONMENT:-unknown}]"

# ------------------------------------------------------- record this service
# Atomic: write to a temp file in the same directory, then rename. A compose
# run from another pipeline never observes a half-written file.
mkdir -p images
tmp=$(mktemp "images/.${SERVICE}.XXXXXX")
printf '%s=%s\n' "$IMAGE_VAR" "$IMAGE_REF" > "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "images/${SERVICE}.env"
echo "==> wrote images/${SERVICE}.env"

# --------------------------------------------------- build COMPOSE_ENV_FILES
# Setting this replaces the automatic .env load entirely, so .env must be
# listed explicitly first or every secret in it silently disappears.
# Absolute paths, so the list does not depend on the working directory.
shopt -s nullglob
image_envs=("$PWD"/images/*.env)
shopt -u nullglob

if [ ${#image_envs[@]} -eq 0 ]; then
    echo "ERROR: no files in $COMPOSE_DIR/images/ - not even this service's"
    exit 1
fi

env_list="$PWD/.env"
for f in "${image_envs[@]}"; do
    env_list="${env_list},${f}"
done

export COMPOSE_ENV_FILES="$env_list"
echo "==> COMPOSE_ENV_FILES=$COMPOSE_ENV_FILES"

# --------------------------------------------------------------- assertion
# NOTE: `config --images <service>` lists the service AND its transitive
# dependencies (migrate tool, postgres, ...), one per line - not a single
# image. So this is a membership test, not an equality test.
#
# With `:?` a missing value already errors out, and a wrong IMAGE_VAR shows up
# as "required variable IRIS_IMAGE is missing a value", so this is mostly a
# cheap sanity check. The real verification happens after `up -d`, against the
# container that actually got created.
if ! images=$(docker compose config --images "$SERVICE" 2>&1); then
    echo "ERROR: compose could not resolve the stack:"
    echo "$images"
    echo
    echo "If this names another service's image variable, that service has"
    echo "never deployed with this mechanism and has no file yet. Seed it"
    echo "once by hand with the ref that is currently running, e.g.:"
    echo "  echo 'LOGIC_IMAGE=registry.../logic@sha256:...' > $COMPOSE_DIR/images/logic.env"
    exit 1
fi

if ! printf '%s\n' "$images" | grep -Fxq -- "$IMAGE_REF"; then
    echo "ERROR: $IMAGE_REF is not in the resolved image set for $SERVICE:"
    printf '  %s\n' $images
    echo "       check that IMAGE_VAR ($IMAGE_VAR) is the variable the compose"
    echo "       file actually interpolates for this service."
    exit 1
fi

# ------------------------------------------------------------- pull and up
# Requires the SSH user's own `docker login` on this host, with credsStore
# removed from its ~/.docker/config.json.
docker compose pull "$SERVICE"

# The migrator is its own compose service on the same image, wired in as a
# depends_on with service_completed_successfully, so `up -d` runs migrations
# first and refuses to start the app if they fail.
#
# A failed migration is the most likely failure here, and set -e would abort
# with only compose's one-line error. Dump recent logs so the migrator's
# output is visible in the build.
if ! docker compose up -d "$SERVICE"; then
    echo "ERROR: compose up failed - dependency or migration likely failed"
    docker compose logs --tail=100 || true
    exit 1
fi

# ----------------------------------------------------------- health gating
# No pipe to `head`: under `set -o pipefail`, head closing the pipe early can
# SIGPIPE compose and fail the pipeline. Trim in the shell instead.
# -a is required: without it, compose lists only RUNNING containers, so a
# service that crashed on startup returns empty and is misreported.
cid=$(docker compose ps -aq "$SERVICE")
cid=${cid%%$'\n'*}
if [ -z "$cid" ]; then
    echo "ERROR: no container was created for $SERVICE"
    docker compose logs --tail=50 "$SERVICE" || true
    exit 1
fi

# Stronger than the pre-flight check: compare image IDs, confirming the
# running container IS the image Jenkins built, not merely that the digest
# appeared somewhere in the resolved config.
expected_id=$(docker image inspect -f '{{.Id}}' "$IMAGE_REF")
actual_id=$(docker inspect -f '{{.Image}}' "$cid")
if [ "$expected_id" != "$actual_id" ]; then
    echo "ERROR: $SERVICE is running the wrong image"
    echo "       container: $actual_id"
    echo "       expected:  $expected_id ($IMAGE_REF)"
    echo "       an old container was probably reused instead of recreated"
    exit 1
fi
echo "==> verified $SERVICE is running $IMAGE_REF"

echo "==> waiting for $SERVICE to settle"

# Without a healthcheck, "running" is true the instant up -d returns, so a
# container that crashes three seconds later would pass. Require it to stay
# running across several consecutive polls instead of exiting on the first.
stable=0
stable_required=5          # 5 x 2s = 10s continuously up

timeout_secs=90
deadline=$(( $(date +%s) + timeout_secs ))

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

echo "ERROR: $SERVICE did not become healthy within ${timeout_secs}s"
docker compose logs --tail=50 "$SERVICE"
exit 1
