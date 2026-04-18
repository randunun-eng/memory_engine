# syntax=docker/dockerfile:1.7
# Go 1.25 — required by latest whatsmeow (@3ff20cd bumped Go floor to 1.25
# at commit 6bd7aa0). We pin whatsmeow to latest to get waVersion
# 2.3000.1037076227; the API-break in lharries' bridge code is patched via
# sed below.
FROM golang:1.25-alpine AS builder

RUN apk add --no-cache git gcc musl-dev sqlite-dev

WORKDIR /src
RUN git clone --depth 1 https://github.com/lharries/whatsapp-mcp.git .

WORKDIR /src/whatsapp-bridge
ENV CGO_ENABLED=1

# Pin whatsmeow to latest stable to get the current waVersion (2.3000.1037076227).
# lharries' original pin (Mar 2025, waVersion 1021018791) gets HTTP 405 from
# WhatsApp; intermediate pre-API-break pins only bump waVersion by ~1.4M which
# is insufficient (WhatsApp has passed through 15+ proto updates since then).
#
# The newer whatsmeow introduced context.Context as the first parameter of
# several DB methods at commit d9bdff4 ("all: use contexts for database access",
# May 2025). lharries' bridge calls one of them — Store.Contacts.GetContact —
# without a ctx arg. Sed-patch main.go to pass context.Background() before go
# build. If other compile errors surface, add more sed rules iteratively.
#
# See twincore-alpha/DRIFT.md `whatsmeow-client-version-drift` and
# `whatsapp-bridge-patched-main-go`.
RUN go get go.mau.fi/whatsmeow@3ff20cd3462a1c134fdc7fdaf95c798b3c28489d && go mod tidy

# Patch: pass context.Background() to every whatsmeow DB/network method that
# gained a ctx first parameter between lharries' pin and the current pin.
# 'context' is already imported in lharries' main.go — verified at this pin.
# Add new sed rules below as more compile errors surface.
RUN set -eux && \
    sed -i 's|client\.Store\.Contacts\.GetContact(jid)|client.Store.Contacts.GetContact(context.Background(), jid)|g' main.go && \
    sed -i 's|client\.Download(downloader)|client.Download(context.Background(), downloader)|g' main.go && \
    sed -i 's|sqlstore\.New("sqlite3",|sqlstore.New(context.Background(), "sqlite3",|g' main.go && \
    sed -i 's|container\.GetFirstDevice()|container.GetFirstDevice(context.Background())|g' main.go && \
    sed -i 's|client\.GetGroupInfo(jid)|client.GetGroupInfo(context.Background(), jid)|g' main.go

RUN go build -o /out/whatsapp-bridge .

# ==========================================================
FROM alpine:3.20

RUN apk add --no-cache ca-certificates sqlite tini

WORKDIR /app/whatsapp-bridge
COPY --from=builder /out/whatsapp-bridge /usr/local/bin/whatsapp-bridge

RUN mkdir -p /app/whatsapp-bridge/store /app/whatsapp-bridge/auth_info

ENV LANG=en_US.UTF-8
ENV LC_ALL=en_US.UTF-8

EXPOSE 8080

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["/usr/local/bin/whatsapp-bridge"]
