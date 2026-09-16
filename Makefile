GO ?= go
LINTER ?= golangci-lint
COMPOSE_DIR ?= /opt/ids-gateway
SERVICE ?= iris
IMAGE_VAR ?= IRIS_IMAGE
SSH_OPTS := -o StrictHostKeyChecking=accept-new \
            -o BatchMode=yes \
            -o ConnectTimeout=10
MAKEFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
DEPLOY_SCRIPT := $(MAKEFILE_DIR)bin/deploy-remote.sh

.PHONY: all format build test lint lint-fix deploy local-spinup local-update

all: build test format lint

format:
	$(GO) fmt ./...

build:
	$(GO) build -o iris ./cmd/iris

test:
	$(GO) test ./...

lint:
	$(LINTER) run

lint-fix:
	$(LINTER) run --fix

deploy:
	@test -n "$(IMAGE)"   || { echo "IMAGE is required";   exit 1; }
	@test -n "$(HOST)"    || { echo "HOST is required";    exit 1; }
	@test -n "$(SSH_KEY)" || { echo "SSH_KEY is required (set by withCredentials)"; exit 1; }
	@test -s "$(DEPLOY_SCRIPT)" || { echo "missing or empty $(DEPLOY_SCRIPT)"; exit 1; }
	@echo "deploying $(SERVICE) to $(HOST) [$(ENVIRONMENT)]"
	ssh -i "$(SSH_KEY)" $(SSH_OPTS) "$(HOST)" \
		IMAGE_VAR="$(IMAGE_VAR)" \
		IMAGE_REF="$(IMAGE)" \
		COMPOSE_DIR="$(COMPOSE_DIR)" \
		SERVICE="$(SERVICE)" \
		ENVIRONMENT="$(ENVIRONMENT)" \
		bash -se < "$(DEPLOY_SCRIPT)"

local:
	if ! docker network inspect local-iris-net >/dev/null 2>&1; then \
		docker network create local-iris-net; \
	fi
	if [ -z "$$(docker ps -a -q -f name=^local-iris-postgres$$)" ]; then \
		echo "WTF IS HAPPENING"; \
		docker run -d --name local-iris-postgres --network local-iris-net --env-file .env postgres; \
	fi
	if [ -n "$$(docker ps -a -q -f name=^local-iris-api$$)" ]; then \
		docker stop local-iris-api; \
		docker rm local-iris-api; \
	fi
	docker build -t iris:local ./
	docker run --rm --network local-iris-net --env-file .env --entrypoint /iris iris:local migrate
	docker run -d --name local-iris-api --network local-iris-net --env-file .env -p "127.0.0.1:8080:8080" -v ./data/pdfs:/pdfs iris:local

local-spinup:
	docker network create iris-test
	docker run -d --name iris-postgres --network iris-test --env-file .env postgres
	docker build -t iris ./
	docker run -d --name iris --network iris-test --env-file .env -p "127.0.0.1:8080:8080" iris

local-update:
	docker build -t iris ./ \
	&& docker stop iris \
	&& docker rm iris \
	&& docker run -d --name iris --network iris-test --env-file .env -p "127.0.0.1:8080:8080" \
		-v ./test_pdfs:/test_pdfs iris
