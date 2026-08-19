GO ?= go
LINTER ?= golangci-lint

.PHONY: all build test lint lint-fix

all: build test lint

build:
	$(GO) build -o iris ./cmd/iris

test:
	$(GO) test ./...

lint:
	$(LINTER) run

lint-fix:
	$(LINTER) run --fix
