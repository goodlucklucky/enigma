FROM golang:1.22 AS builder
WORKDIR /src

FROM alpine:3.19
COPY --from=not_a_real_stage /out/app /usr/local/bin/app
