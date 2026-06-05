FROM node:22-slim AS frontend-build

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    FRONTEND_DIST=/app/frontend/dist

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        fonts-dejavu-core \
        gifsicle \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist

RUN mkdir -p /data/uploads /data/downloads /data/outputs /data/cookies /data/fonts

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
