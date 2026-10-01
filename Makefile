GO ?= go
GOFMT ?= gofmt "-s"
SQLC ?= $(GO) tool sqlc
OAPI_CODEGEN ?= $(GO) tool oapi-codegen
BINARY=iris
MAIN_PACKAGE=cmd/iris
GOFILES := $(shell find . -name "*.go")
SQL_GEN_IN := sqlc.yaml query.sql $(wildcard migrations/*.sql) go.mod go.sum
SQL_GEN_OUT := data/db.go data/models.go data/query.sql.go
OAPI_GEN_CONFIG := openapi/oapi-codegen.yaml
OAPI_SPEC := openapi/open-api.yaml
OAPI_GEN_IN := $(OAPI_GEN_CONFIG) $(OAPI_SPEC) go.mod go.sum
OAPI_GEN_OUT := api/api.gen.go

COMPOSE_DIR ?= /opt/ids-gateway
SERVICE ?= iris
IMAGE_VAR ?= IRIS_IMAGE
SSH_OPTS := -o StrictHostKeyChecking=accept-new \
            -o BatchMode=yes \
            -o ConnectTimeout=10
MAKEFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
DEPLOY_SCRIPT := $(MAKEFILE_DIR)bin/deploy-remote.sh

.PHONY: all generate generate-sqlc generate-oapi format build test lint lint-fix deploy local tools-update clean

all: clean build test format lint

generate: $(SQL_GEN_OUT) $(OAPI_GEN_OUT)

generate-sqlc: $(SQL_GEN_OUT)

generate-oapi: $(OAPI_GEN_OUT)

$(SQL_GEN_OUT) &: $(SQL_GEN_IN)
	$(SQLC) generate

$(OAPI_GEN_OUT): $(OAPI_GEN_IN)
	$(OAPI_CODEGEN) -config $(OAPI_GEN_CONFIG) $(OAPI_SPEC)

format:
	$(GOFMT) -w $(GOFILES)

$(BINARY): $(SQL_GEN_OUT) $(OAPI_GEN_OUT) $(GOFILES)
	$(GO) build -v -o $(BINARY) ./$(MAIN_PACKAGE)

build: $(BINARY)

test: generate
	$(GO) test ./...

lint: generate
	$(GO) run github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest run

lint-fix: generate
	$(GO) run github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest run --fix

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
		docker run -d --name local-iris-postgres --network local-iris-net --env-file .env postgres; \
	fi
	if [ -n "$$(docker ps -a -q -f name=^local-iris-api$$)" ]; then \
		docker stop local-iris-api; \
		docker rm local-iris-api; \
	fi
	docker build -t iris:local ./
	docker run --rm --network local-iris-net --env-file .env --entrypoint /iris iris:local migrate
	docker run -d --name local-iris-api --network local-iris-net --env-file .env -p "127.0.0.1:8080:8080" -v ./data/pdfs:/pdfs iris:local

tools-update:
	$(GO) get -tool github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@latest
	$(GO) get -tool github.com/sqlc-dev/sqlc/cmd/sqlc@latest
	$(GO) mod tidy

clean:
	rm -f $(BINARY) \
		$(SQL_GEN_OUT) \
		$(OAPI_GEN_OUT)
