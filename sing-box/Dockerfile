ARG SING_BOX_VERSION=1.14.0
ARG BUILDPLATFORM
FROM --platform=${BUILDPLATFORM} golang:1.26-alpine AS builder
ARG SING_BOX_VERSION
ARG TARGETOS
ARG TARGETARCH
RUN apk add --no-cache git build-base
RUN git clone --depth 1 --branch "v${SING_BOX_VERSION}" https://github.com/SagerNet/sing-box.git /src/sing-box
WORKDIR /src/sing-box
COPY anytls-source-ip.patch /tmp/anytls-source-ip.patch
RUN git apply --check /tmp/anytls-source-ip.patch && git apply /tmp/anytls-source-ip.patch
RUN TAGS="$(cat release/DEFAULT_BUILD_TAGS_OTHERS),with_v2ray_api" && \
    GOOS="${TARGETOS:-linux}" GOARCH="${TARGETARCH:-$(go env GOARCH)}" CGO_ENABLED=0 \
    go build -trimpath -tags "$TAGS" -ldflags "-s -w -X github.com/sagernet/sing-box/constant.Version=${SING_BOX_VERSION}" -o /out/sing-box ./cmd/sing-box && \
    printf 'sing-box=%s\ncommit=%s\nos=%s\narch=%s\ntags=%s\n' "$SING_BOX_VERSION" "$(git rev-parse HEAD)" "${TARGETOS:-linux}" "${TARGETARCH:-$(go env GOARCH)}" "$TAGS" > /out/build-info.txt

FROM fullstorydev/grpcurl:v1.9.3-alpine AS grpcurl

FROM alpine:3.22
ARG SING_BOX_VERSION
LABEL org.opencontainers.image.source="https://github.com/xiaofujie369/xboard-singbox-anyreality-V1" \
      org.opencontainers.image.version="1.2.0" \
      org.opencontainers.image.description="sing-box with V2Ray Stats API for XBoard AnyTLS/VLESS Reality"
RUN apk add --no-cache ca-certificates tzdata
COPY --from=builder /out/sing-box /usr/local/bin/sing-box
COPY --from=builder /out/build-info.txt /usr/local/share/sing-box/build-info.txt
COPY --from=grpcurl /bin/grpcurl /usr/local/bin/grpcurl
COPY stats.proto /usr/local/share/sing-box/stats.proto
ENTRYPOINT ["sing-box"]
