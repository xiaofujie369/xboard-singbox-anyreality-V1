ARG SING_BOX_VERSION=1.14.0
FROM golang:1.26-alpine AS builder
ARG SING_BOX_VERSION
RUN apk add --no-cache git build-base
RUN git clone --depth 1 --branch "v${SING_BOX_VERSION}" https://github.com/SagerNet/sing-box.git /src/sing-box
WORKDIR /src/sing-box
RUN TAGS="$(cat release/DEFAULT_BUILD_TAGS_OTHERS),with_v2ray_api" && \
    go build -trimpath -tags "$TAGS" -ldflags "-s -w -X github.com/sagernet/sing-box/constant.Version=${SING_BOX_VERSION}" -o /out/sing-box ./cmd/sing-box

FROM fullstorydev/grpcurl:v1.9.3-alpine AS grpcurl

FROM alpine:3.22
RUN apk add --no-cache ca-certificates tzdata
COPY --from=builder /out/sing-box /usr/local/bin/sing-box
COPY --from=grpcurl /bin/grpcurl /usr/local/bin/grpcurl
COPY stats.proto /usr/local/share/sing-box/stats.proto
ENTRYPOINT ["sing-box"]
