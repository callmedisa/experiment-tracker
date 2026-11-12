FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
# dev requirements are installed too so tests can run in this image (`docker compose run --rm api pytest`)
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app_factory --factory --host 0.0.0.0 --port 8000"]
