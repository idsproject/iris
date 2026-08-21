GO ?= go
LINTER ?= golangci-lint

.PHONY: all build test lint lint-fix deploy dev-spinup

all: build test lint

build:
	$(GO) build -o iris ./cmd/iris

test:
	$(GO) test ./...

lint:
	$(LINTER) run

lint-fix:
	$(LINTER) run -fix

deploy:
	echo "Deploying..."

dev-spinup:
	docker network create iris-test
	docker run -d --name iris-postgres --network iris-test --env-file .env postgres
	docker build -t iris ./
	docker run -d --name iris --network iris-test --env-file .env -p "127.0.0.1:8080:8080" iris
