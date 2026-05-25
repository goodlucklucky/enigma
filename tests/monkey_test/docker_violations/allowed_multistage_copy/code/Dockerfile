FROM golang:1.22 AS builder
WORKDIR /src
COPY main.go .
RUN go build -o /out/app main.go

FROM alpine:3.19
COPY --from=builder /out/app /usr/local/bin/app
CMD ["app"]
