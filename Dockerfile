FROM golang:1.26 AS build

WORKDIR /app

COPY go.mod go.sum /

RUN --mount=type=cache,target=/go/pkg/mod \
  go mod download

COPY . .

RUN --mount=type=cache,sharing=shared,target=/go/pkg/mod \
    --mount=type=cache,sharing=shared,target=/root/.cache/go-build \
    GOOS=linux \
    go build -o /iris ./cmd/iris

RUN useradd -s /sbin/nologin -M -U iris-user

FROM yeslogic/prince:17-debian-13-slim

RUN apt-get -y update && apt-get -y install ca-certificates curl

COPY license.dat /usr/lib/prince/license/license.dat

COPY migrations/ /migrations
ENV MIGRATIONS_DIR=file:///migrations

COPY robots.txt /robots.txt

COPY --from=build /usr/share/zoneinfo /usr/share/zoneinfo
COPY --from=build /etc/passwd /etc/passwd
COPY --from=build /etc/group /etc/group

COPY --from=build /iris /iris

USER iris-user:iris-user
CMD ["/iris"]
