FROM node:22-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./
COPY --from=frontend-builder /frontend/dist ./frontend/dist

RUN groupadd --system app && useradd --system --gid app --uid 10001 app \
    && mkdir -p /app/out && chown -R app:app /app
USER app

ENTRYPOINT ["python", "starter.py"]
CMD ["--data", "/app/data", "--out", "/app/out"]
