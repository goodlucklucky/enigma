FROM python:3.12-slim
WORKDIR /app
COPY --from=nginx:latest /etc/nginx/nginx.conf /app/nginx.conf
COPY app.py /app/
CMD ["python", "app.py"]
