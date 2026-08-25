GO ?= go
LINTER ?= golangci-lint

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
	echo "Deploying..."

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
