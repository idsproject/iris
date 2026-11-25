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

# The license comes in as a BuildKit secret (see `make image`), so it never
# has to be in the build context. Secret contents aren't part of the cache
# key; PRINCE_LICENSE_SHA is, so a renewed license rebuilds this layer instead
# of the registry cache serving the old one. install -m 0644 because the
# secret mount is 0400 root and Prince runs as iris-user.
ARG PRINCE_LICENSE_SHA
RUN --mount=type=secret,id=prince_license,required=true \
    install -D -m 0644 /run/secrets/prince_license /usr/lib/prince/license/license.dat

ENV XDG_CACHE_HOME=/var/cache/iris

COPY migrations/ /migrations
ENV MIGRATIONS_DIR=file:///migrations

COPY robots.txt /robots.txt

COPY --from=build /usr/share/zoneinfo /usr/share/zoneinfo
COPY --from=build /etc/passwd /etc/passwd
COPY --from=build /etc/group /etc/group

COPY --from=build /iris /iris

RUN install -d -o iris-user -g iris-user "$XDG_CACHE_HOME"
USER iris-user:iris-user
RUN echo '<p></p>' | prince - -o /dev/null

ENTRYPOINT ["/iris"]
