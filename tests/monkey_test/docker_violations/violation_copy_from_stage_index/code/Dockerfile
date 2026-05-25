FROM python:3.12-slim
WORKDIR /app
COPY --from=1 /out/app /app/app
CMD ["python", "-c", "print('ok')"]
